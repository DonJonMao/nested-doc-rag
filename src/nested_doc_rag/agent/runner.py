from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from nested_doc_rag.excel.writeback import patch_workbook
from nested_doc_rag.io import read_jsonl, write_json, write_jsonl
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction

from .policies import (
    build_query_plan,
    make_prediction_from_evidence,
    retrieve_from_mini_corpus,
    select_evidence,
    should_generate_answer,
    should_human_review,
    should_repair,
    validate_prediction_light,
    with_validation,
)
from .repair import repair_prediction_once
from .state import FieldState, RunState
from .trace import TraceRecorder, now_iso

WRITEBACK_SKIPPED_NO_TEMPLATE = "skipped: template path was not provided"
WRITEBACK_SKIPPED_MISSING_TEMPLATE = "skipped: template file does not exist"
WRITEBACK_SKIPPED_DISABLED = "skipped: writeback disabled"


class FieldFillingAgent:
    def __init__(
        self,
        *,
        target_namespace: str,
        corpus: list[dict[str, Any]],
        out_dir: Path,
        config: Any | None = None,
        room_context: str | None = None,
        max_repair_attempts: int = 1,
        template_path: Path | None = None,
        writeback_enabled: bool = True,
    ):
        self.target_namespace = target_namespace
        self.corpus = corpus
        self.out_dir = out_dir
        self.config = config
        self.room_context = room_context
        self.max_repair_attempts = max(0, min(max_repair_attempts, 1))
        self.template_path = template_path
        self.writeback_enabled = writeback_enabled
        self.run_id = f"agent_{uuid4().hex[:12]}"
        self.trace = TraceRecorder(self.run_id)
        self.field_states: list[FieldState] = []
        self.review_items: list[dict[str, Any]] = []
        self.writeback_status = WRITEBACK_SKIPPED_NO_TEMPLATE
        self.writeback_summary: dict[str, Any] | None = None

    def run(self, fields: list[FieldGold]) -> list[FieldPrediction]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        run_state = RunState(
            run_id=self.run_id,
            target_namespace=self.target_namespace,
            out_dir=self.out_dir,
            fields_total=len(fields),
            started_at=now_iso(),
        )
        predictions: list[FieldPrediction] = []

        for field in fields:
            state = self.create_field_state(field)
            self.trace.record(field.field_id, "field_started", {"field": minimal_field_view(field)})

            state.query_plan = build_query_plan(
                field,
                target_namespace=self.target_namespace,
                room_context=self.room_context,
                config=self.config,
            )
            state.status = "planned"
            self.trace.record(field.field_id, "query_planned", {"query_plan": state.query_plan.to_dict()})

            state.retrieved_chunks = retrieve_from_mini_corpus(state.query_plan, self.corpus, field)
            state.status = "retrieved"
            self.trace.record(field.field_id, "evidence_retrieved", {"chunks": state.retrieved_chunks})

            state.evidence_bundle = select_evidence(state.retrieved_chunks, field, state.query_plan)
            state.status = "evidence_selected"
            self.trace.record(field.field_id, "evidence_selected", {"evidence_bundle": state.evidence_bundle.to_dict()})

            state.draft_prediction = make_prediction_from_evidence(field, state.evidence_bundle)
            if should_generate_answer(state.evidence_bundle):
                state.status = "generated"
                self.trace.record(field.field_id, "answer_generated", {"prediction": state.draft_prediction.to_dict()})
            else:
                state.status = "human_review"
                self.trace.record(field.field_id, "answer_skipped", {"prediction": state.draft_prediction.to_dict()})

            validation = validate_prediction_light(field, state.draft_prediction, self.config)
            state.draft_prediction = with_validation(state.draft_prediction, validation)
            state.validation_result = validation
            state.status = "validated"
            self.trace.record(field.field_id, "validated", {"validation_result": validation.to_dict()})

            repair_decision = should_repair(validation, state, max_attempts=self.max_repair_attempts)
            final_prediction = state.draft_prediction
            if repair_decision.should_repair:
                repaired_prediction, repair_log = repair_prediction_once(field, state.draft_prediction, validation)
                state.repair_attempts.append(repair_log)
                repaired_validation = validate_prediction_light(field, repaired_prediction, self.config)
                final_prediction = with_validation(repaired_prediction, repaired_validation)
                state.validation_result = repaired_validation
                state.status = "repaired"
                self.trace.record(
                    field.field_id,
                    "repaired",
                    {
                        "repair_decision": repair_decision.to_dict(),
                        "repair_log": repair_log,
                        "validation_result": repaired_validation.to_dict(),
                    },
                )
            else:
                self.trace.record(field.field_id, "repair_skipped", {"repair_decision": repair_decision.to_dict()})

            state.final_prediction = final_prediction
            human_review = should_human_review(state)
            if human_review:
                state.status = "human_review"
                run_state.fields_human_review += 1
                review_item = make_review_item(state)
                self.review_items.append(review_item)
                self.trace.record(field.field_id, "human_review_required", {"reason": review_item["reason"], "review_item": review_item})
            else:
                state.status = "completed"
                self.trace.record(field.field_id, "human_review_skipped", {"reason": "direct evidence validated"})

            run_state.fields_completed += 1
            self.field_states.append(state)
            predictions.append(final_prediction)
            self.trace.record(
                field.field_id,
                "field_completed",
                {
                    "status": state.status,
                    "final_prediction": final_prediction.to_dict(),
                    "human_review": human_review,
                    "repair_attempts": state.repair_attempts,
                },
            )

        run_state.finished_at = now_iso()
        self.write_outputs(predictions, run_state)
        return predictions

    def create_field_state(self, field: FieldGold) -> FieldState:
        return FieldState(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            question_text=field.question_text,
            field_type=field.field_type,
            required=field.required,
            must_have_evidence=field.must_have_evidence,
            constraints=field.constraints,
        )

    def write_outputs(self, predictions: list[FieldPrediction], run_state: RunState) -> None:
        predictions_path = self.out_dir / "predictions.jsonl"
        trace_path = self.out_dir / "trace.jsonl"
        trace_summary_path = self.out_dir / "trace_summary.json"
        trace_md_path = self.out_dir / "trace.md"
        review_items_path = self.out_dir / "review_items.jsonl"
        write_jsonl(predictions_path, [prediction.to_dict() for prediction in predictions])
        write_jsonl(review_items_path, self.review_items)
        self.trace.write_jsonl(trace_path)
        self.trace.write_summary(trace_summary_path)
        self.trace.write_markdown(trace_md_path)
        self.maybe_writeback(predictions)
        listed_outputs = sorted({*output_files(self.out_dir), "run_summary.md", "run_state.json"})
        run_summary = build_run_summary(
            run_state=run_state,
            trace_summary=self.trace.summary(),
            review_count=len(read_jsonl(review_items_path)),
            output_files=listed_outputs,
            writeback_status=self.writeback_status,
            writeback_summary=self.writeback_summary,
        )
        (self.out_dir / "run_summary.md").write_text(run_summary, encoding="utf-8")
        write_json(self.out_dir / "run_state.json", run_state.to_dict())

    def maybe_writeback(self, predictions: list[FieldPrediction]) -> None:
        review_items_path = self.out_dir / "review_items.jsonl"
        agent_review_items = read_jsonl(review_items_path)
        if not self.writeback_enabled:
            self.writeback_status = WRITEBACK_SKIPPED_DISABLED
            return
        if self.template_path is None:
            self.writeback_status = WRITEBACK_SKIPPED_NO_TEMPLATE
            return
        if not self.template_path.exists():
            self.writeback_status = WRITEBACK_SKIPPED_MISSING_TEMPLATE
            return

        summary = patch_workbook(
            template_path=self.template_path,
            predictions=predictions,
            output_path=self.out_dir / "filled_form.xlsx",
            trace_by_field={prediction.field_id: f"{self.run_id}:{prediction.field_id}" for prediction in predictions},
        )
        writeback_review_items = read_jsonl(review_items_path)
        merged_review_items = merge_review_items(agent_review_items, writeback_review_items)
        write_jsonl(review_items_path, merged_review_items)
        self.writeback_status = "completed"
        self.writeback_summary = summary.to_dict()


def load_fields(path: Path) -> list[FieldGold]:
    return [FieldGold.from_dict(record) for record in read_jsonl(path)]


def load_corpus(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)


def minimal_field_view(field: FieldGold) -> dict[str, Any]:
    return {
        "field_id": field.field_id,
        "row_index": field.row_index,
        "target_cell": field.target_cell,
        "question_text": field.question_text,
        "field_type": field.field_type,
        "required": field.required,
        "must_have_evidence": field.must_have_evidence,
    }


def make_review_item(state: FieldState) -> dict[str, Any]:
    prediction = state.final_prediction or state.draft_prediction
    candidate_ids = [str(chunk.get("chunk_id")) for chunk in state.retrieved_chunks if chunk.get("chunk_id")]
    return {
        "field_id": state.field_id,
        "row_index": state.row_index,
        "target_cell": state.target_cell,
        "question_text": state.question_text,
        "answer_status": prediction.answer_status if prediction else "not_found",
        "proposed_answer": prediction.answer_value if prediction else "未找到",
        "reason": human_review_reason(state),
        "candidate_chunk_ids": candidate_ids,
        "suggested_action": "人工确认",
    }


def human_review_reason(state: FieldState) -> str:
    if state.evidence_bundle and state.evidence_bundle.conflict_detected:
        return "conflict_unresolved"
    prediction = state.final_prediction or state.draft_prediction
    if prediction and prediction.answer_status in {"partial_clue", "not_found", "conflict_unresolved"}:
        return prediction.answer_status
    if state.validation_result and state.validation_result.violations:
        return ",".join(state.validation_result.violations)
    if state.must_have_evidence and prediction and not prediction.source_chunk_ids:
        return "missing_evidence"
    return "needs_human_review"


def merge_review_items(agent_items: list[dict[str, Any]], writeback_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source, items in [("agent", agent_items), ("writeback", writeback_items)]:
        for item in items:
            field_id = str(item.get("field_id") or "")
            reason = str(item.get("reason") or "")
            key = (field_id, reason)
            if key in seen:
                continue
            seen.add(key)
            merged.append({"source": source, **item})
    return merged


def output_files(out_dir: Path) -> list[str]:
    return sorted(path.name for path in out_dir.iterdir() if path.is_file())


def build_run_summary(
    *,
    run_state: RunState,
    trace_summary: dict[str, Any],
    review_count: int,
    output_files: list[str],
    writeback_status: str,
    writeback_summary: dict[str, Any] | None,
) -> str:
    lines = [
        "# Field Filling Agent Run Summary",
        "",
        f"- run_id: `{run_state.run_id}`",
        f"- target_namespace: `{run_state.target_namespace}`",
        f"- started_at: `{run_state.started_at}`",
        f"- finished_at: `{run_state.finished_at}`",
        f"- total_fields: {run_state.fields_total}",
        f"- answered: {trace_summary['answered_count']}",
        f"- partial_clue: {trace_summary['partial_clue_count']}",
        f"- not_found: {trace_summary['not_found_count']}",
        f"- conflict_unresolved: {trace_summary['conflict_unresolved_count']}",
        f"- human_review: {review_count}",
        f"- repaired: {trace_summary['repaired_count']}",
        f"- writeback: {writeback_status}",
        "",
        "## Evidence Strategy",
        "",
        "target namespace 的 main_excel_capability 优先于 global intro，global 只作为 reference clue。",
        "存在同级冲突时不自动裁决为答案，进入人工审核。",
        "",
        "## Output Files",
        "",
    ]
    lines.extend(f"- `{file_name}`" for file_name in output_files)
    if writeback_summary:
        lines.extend(["", "## Writeback", "", f"- filled_form: `{writeback_summary.get('output_path')}`"])
    return "\n".join(lines).rstrip() + "\n"
