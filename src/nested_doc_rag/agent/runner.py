from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from nested_doc_rag.agent.backends import AnswerGenerator, DeterministicAnswerGenerator, EvidenceRetriever, MiniCorpusRetriever
from nested_doc_rag.excel.writeback import patch_workbook
from nested_doc_rag.io import read_jsonl, write_json, write_jsonl
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction

from .policies import (
    build_query_plan,
    make_prediction_from_evidence,
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
        corpus: list[dict[str, Any]] | None = None,
        out_dir: Path,
        config: Any | None = None,
        room_context: str | None = None,
        max_repair_attempts: int = 1,
        template_path: Path | None = None,
        writeback_enabled: bool = True,
        retriever: EvidenceRetriever | None = None,
        answer_generator: AnswerGenerator | None = None,
        retrieval_backend: str = "mini",
        generation_backend: str = "deterministic",
        enable_rerank: bool = False,
        resume: bool = False,
        checkpoint_every: int = 1,
        checkpoint_path: Path | None = None,
    ):
        self.target_namespace = target_namespace
        self.corpus = corpus or []
        self.out_dir = out_dir
        self.config = config
        self.room_context = room_context
        self.max_repair_attempts = max(0, min(max_repair_attempts, 1))
        self.template_path = template_path
        self.writeback_enabled = writeback_enabled
        self.retriever = retriever or MiniCorpusRetriever(self.corpus)
        self.answer_generator = answer_generator or DeterministicAnswerGenerator()
        self.retrieval_backend = retrieval_backend
        self.generation_backend = generation_backend
        self.enable_rerank = enable_rerank
        self.resume = resume
        self.checkpoint_every = max(1, checkpoint_every)
        self.checkpoint_path = checkpoint_path
        self.run_id = f"agent_{uuid4().hex[:12]}"
        self.trace = TraceRecorder(self.run_id, metadata=self.run_metadata())
        self.field_states: list[FieldState] = []
        self.review_items: list[dict[str, Any]] = []
        self.writeback_status = WRITEBACK_SKIPPED_NO_TEMPLATE
        self.writeback_summary: dict[str, Any] | None = None

    def run(self, fields: list[FieldGold]) -> list[FieldPrediction]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_predictions = self.load_checkpoint_predictions() if self.resume else {}
        if self.resume:
            self.load_checkpoint_sidecars()
        skipped_completed_count = sum(1 for field in fields if field.field_id in checkpoint_predictions)
        run_state = RunState(
            run_id=self.run_id,
            target_namespace=self.target_namespace,
            out_dir=self.out_dir,
            fields_total=len(fields),
            fields_completed=skipped_completed_count,
            started_at=now_iso(),
            resumed_count=1 if self.resume and checkpoint_predictions else 0,
            skipped_completed_count=skipped_completed_count,
        )
        self.trace.record(None, "run_started", self.run_metadata())
        if self.resume and checkpoint_predictions:
            self.trace.record(
                None,
                "resume_started",
                {
                    "prediction_checkpoint": str(self.predictions_checkpoint_path()),
                    "skipped_completed_count": skipped_completed_count,
                    "completed_field_ids": sorted(checkpoint_predictions),
                },
            )
        predictions_by_field_id: dict[str, FieldPrediction] = dict(checkpoint_predictions)
        processed_since_checkpoint = 0

        for field in fields:
            if field.field_id in checkpoint_predictions:
                continue
            try:
                prediction, state, human_review = self.process_field(field)
            except Exception as exc:  # noqa: BLE001 - keep one field failure from aborting the whole run
                prediction, state, human_review = self.failed_field_prediction(field, exc)
                run_state.fields_failed += 1

            if human_review:
                run_state.fields_human_review += 1
            run_state.fields_completed += 1
            self.field_states.append(state)
            predictions_by_field_id[field.field_id] = prediction
            processed_since_checkpoint += 1
            if processed_since_checkpoint >= self.checkpoint_every:
                self.write_checkpoint(fields, predictions_by_field_id, run_state)
                processed_since_checkpoint = 0

        run_state.finished_at = now_iso()
        self.trace.record(None, "run_completed", run_state.to_dict())
        ordered_predictions = ordered_predictions_for_fields(fields, predictions_by_field_id)
        self.write_checkpoint(fields, predictions_by_field_id, run_state)
        self.write_outputs(ordered_predictions, run_state)
        return ordered_predictions

    def process_field(self, field: FieldGold) -> tuple[FieldPrediction, FieldState, bool]:
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

        retrieval_started = perf_counter_ms()
        state.retrieved_chunks = self.retriever.retrieve(state.query_plan, field)
        retrieval_latency_ms = round(perf_counter_ms() - retrieval_started, 3)
        retrieval_metadata = dict(getattr(self.retriever, "last_metadata", {}) or {})
        state.status = "retrieved"
        self.trace.record(
            field.field_id,
            "evidence_retrieved",
            {
                "retrieval_backend": self.retrieval_backend,
                "retrieval_latency_ms": retrieval_latency_ms,
                "retrieval_hit_count": len(state.retrieved_chunks),
                "retrieval_metadata": retrieval_metadata,
                "chunks": state.retrieved_chunks,
            },
        )

        state.evidence_bundle = select_evidence(state.retrieved_chunks, field, state.query_plan)
        state.status = "evidence_selected"
        self.trace.record(
            field.field_id,
            "evidence_selected",
            {
                "evidence_bundle": state.evidence_bundle.to_dict(),
                "selected_chunk_ids": chunk_ids(state.evidence_bundle.selected_chunks),
                "reference_chunk_ids": chunk_ids(state.evidence_bundle.reference_chunks),
                "ignored_chunk_ids": chunk_ids(state.evidence_bundle.ignored_chunks),
            },
        )

        generation_called = should_generate_answer(state.evidence_bundle)
        generation_skip_reason = generation_skip_reason_for_bundle(state.evidence_bundle)
        generation_started = perf_counter_ms()
        if generation_called:
            state.draft_prediction = self.answer_generator.generate(
                field,
                state.evidence_bundle,
                state.query_plan,
                trace_context={"run_id": self.run_id, "field_id": field.field_id},
            )
            generation_latency_ms = round(perf_counter_ms() - generation_started, 3)
            state.status = "generated"
            self.trace.record(
                field.field_id,
                "answer_generated",
                {
                    "generation_backend": self.generation_backend,
                    "generation_called": True,
                    "generation_skip_reason": "direct_evidence_llm_called" if self.generation_backend == "llm" else "deterministic_generation",
                    "generation_latency_ms": generation_latency_ms,
                    "prediction": state.draft_prediction.to_dict(),
                },
            )
        else:
            state.draft_prediction = make_prediction_from_evidence(field, state.evidence_bundle)
            state.status = "human_review"
            self.trace.record(
                field.field_id,
                "answer_skipped",
                {
                    "generation_backend": self.generation_backend,
                    "generation_called": False,
                    "generation_skip_reason": generation_skip_reason,
                    "reason": state.evidence_bundle.decision,
                    "prediction": state.draft_prediction.to_dict(),
                },
            )

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
            review_item = make_review_item(state)
            self.review_items.append(review_item)
            self.trace.record(field.field_id, "human_review_required", {"reason": review_item["reason"], "review_item": review_item})
        else:
            state.status = "completed"
            self.trace.record(field.field_id, "human_review_skipped", {"reason": "direct evidence validated"})

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
        return final_prediction, state, human_review

    def failed_field_prediction(self, field: FieldGold, exc: Exception) -> tuple[FieldPrediction, FieldState, bool]:
        state = self.create_field_state(field)
        state.status = "failed"
        prediction = FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value="未找到",
            answer_status="conflict_unresolved",
            confidence=0.0,
            source_chunk_ids=[],
            evidence_attachment_ids=[],
            validation={"error": str(exc), "failed_step": state.status, "needs_human_review": True},
            method_name="field_filling_agent_failed",
        )
        state.final_prediction = prediction
        review_item = make_review_item(state)
        self.review_items.append(review_item)
        self.trace.record(field.field_id, "field_failed", {"error": str(exc), "final_prediction": prediction.to_dict()})
        self.trace.record(field.field_id, "human_review_required", {"reason": review_item["reason"], "review_item": review_item})
        self.trace.record(field.field_id, "field_completed", {"status": state.status, "final_prediction": prediction.to_dict(), "human_review": True})
        return prediction, state, True

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

    def load_checkpoint_predictions(self) -> dict[str, FieldPrediction]:
        checkpoint = self.predictions_checkpoint_path()
        if not checkpoint.exists():
            return {}
        predictions: dict[str, FieldPrediction] = {}
        for record in read_jsonl(checkpoint):
            prediction = FieldPrediction.from_dict(record)
            predictions[prediction.field_id] = prediction
        return predictions

    def load_checkpoint_sidecars(self) -> None:
        review_path = self.review_checkpoint_path()
        if review_path.exists():
            self.review_items = read_jsonl(review_path)
        self.trace.load_jsonl(self.trace_checkpoint_path())

    def write_checkpoint(
        self,
        fields: list[FieldGold],
        predictions_by_field_id: dict[str, FieldPrediction],
        run_state: RunState,
    ) -> None:
        predictions = ordered_predictions_for_fields(fields, predictions_by_field_id)
        write_jsonl(self.predictions_checkpoint_path(), [prediction.to_dict() for prediction in predictions])
        write_jsonl(self.review_checkpoint_path(), self.review_items)
        self.trace.write_jsonl(self.trace_checkpoint_path())
        write_json(self.out_dir / "run_state.json", run_state.to_dict())

    def predictions_checkpoint_path(self) -> Path:
        return self.checkpoint_path or self.out_dir / "predictions.checkpoint.jsonl"

    def trace_checkpoint_path(self) -> Path:
        return self.out_dir / "trace.checkpoint.jsonl"

    def review_checkpoint_path(self) -> Path:
        return self.out_dir / "review_items.checkpoint.jsonl"

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

    def run_metadata(self) -> dict[str, Any]:
        return {
            "retrieval_backend": self.retrieval_backend,
            "retrieval_plan": getattr(self.retriever, "retrieval_plan", ""),
            "generation_backend": self.generation_backend,
            "enable_rerank": self.enable_rerank,
            "target_namespace": self.target_namespace,
            "qdrant_collection": getattr(getattr(self.retriever, "qdrant_retriever", None), "collection_name", ""),
            "chat_model": getattr(self.answer_generator, "chat_model", ""),
            "rerank_enabled": self.enable_rerank,
        }


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


def ordered_predictions_for_fields(fields: list[FieldGold], predictions_by_field_id: dict[str, FieldPrediction]) -> list[FieldPrediction]:
    ordered = [predictions_by_field_id[field.field_id] for field in fields if field.field_id in predictions_by_field_id]
    return sorted(ordered, key=lambda prediction: (prediction.row_index, prediction.field_id))


def generation_skip_reason_for_bundle(bundle: Any) -> str:
    if bundle.decision == "no_evidence":
        return "no_evidence"
    if bundle.decision == "clue_only":
        return "reference_only"
    if bundle.decision == "conflict_unresolved":
        return "conflict"
    return ""


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
        f"- retrieval_backend: `{trace_summary.get('retrieval_backend', '')}`",
        f"- retrieval_plan: `{trace_summary.get('retrieval_plan', '')}`",
        f"- generation_backend: `{trace_summary.get('generation_backend', '')}`",
        f"- enable_rerank: `{trace_summary.get('enable_rerank', False)}`",
        f"- qdrant_collection: `{trace_summary.get('qdrant_collection', '')}`",
        f"- chat_model: `{trace_summary.get('chat_model', '')}`",
        f"- started_at: `{run_state.started_at}`",
        f"- finished_at: `{run_state.finished_at}`",
        f"- total_fields: {run_state.fields_total}",
        f"- answered: {trace_summary['answered_count']}",
        f"- partial_clue: {trace_summary['partial_clue_count']}",
        f"- not_found: {trace_summary['not_found_count']}",
        f"- conflict_unresolved: {trace_summary['conflict_unresolved_count']}",
        f"- human_review: {review_count}",
        f"- repaired: {trace_summary['repaired_count']}",
        f"- generation_called: {trace_summary.get('generation_called_count', 0)}",
        f"- generation_skipped: {trace_summary.get('generation_skipped_count', 0)}",
        f"- skipped_no_evidence: {trace_summary.get('skipped_no_evidence_count', 0)}",
        f"- skipped_reference_only: {trace_summary.get('skipped_reference_only_count', 0)}",
        f"- skipped_conflict: {trace_summary.get('skipped_conflict_count', 0)}",
        f"- direct_evidence: {trace_summary.get('direct_evidence_count', 0)}",
        f"- reference_only: {trace_summary.get('reference_only_count', 0)}",
        f"- resumed_count: {run_state.resumed_count}",
        f"- skipped_completed_count: {run_state.skipped_completed_count}",
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


def perf_counter_ms() -> float:
    from time import perf_counter

    return perf_counter() * 1000


def chunk_ids(chunks: list[dict[str, Any]]) -> list[str]:
    return [str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")]
