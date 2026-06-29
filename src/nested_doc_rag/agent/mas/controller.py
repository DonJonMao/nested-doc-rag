from __future__ import annotations

from pathlib import Path
from typing import Any

from nested_doc_rag.io import write_jsonl

from .agentscope_bridge import AgentScopeRuntime, build_agentscope_runtime
from .roles import AnswerArbitrationRole, EvidenceRetrievalRole, OverlayControlRole, QueryPlannerRole
from .trace import MASTraceRecorder


class Step15MASController:
    def __init__(self, runner: Any, *, mode: str, agentscope_enabled: bool = False) -> None:
        self.runner = runner
        self.mode = mode
        self.trace = MASTraceRecorder()
        self.runtime: AgentScopeRuntime = build_agentscope_runtime(enabled=agentscope_enabled)
        self.query_planner = QueryPlannerRole(runner)
        self.evidence_retrieval = EvidenceRetrievalRole(runner)
        self.answer_arbitration = AnswerArbitrationRole(runner)
        self.overlay_control = OverlayControlRole(runner)

    def run_query_planner(self, item: dict[str, Any]) -> Any:
        return self.runtime.run_role(
            self.query_planner.name,
            _role_payload(item, stage="query_planner"),
            lambda: self.query_planner.run(item),
        )

    def run_evidence_retrieval(self, item: dict[str, Any], query_text: str) -> Any:
        return self.runtime.run_role(
            self.evidence_retrieval.name,
            {**_role_payload(item, stage="evidence_retrieval"), "query_length": len(query_text)},
            lambda: self.evidence_retrieval.run(query_text),
        )

    def run_answer_arbitration(
        self,
        item: dict[str, Any],
        query_text: str,
        top_hits: list[dict[str, Any]],
        *,
        slot_schema: dict[str, Any] | None = None,
    ) -> Any:
        return self.runtime.run_role(
            self.answer_arbitration.name,
            {
                **_role_payload(item, stage="answer_arbitration"),
                "query_length": len(query_text),
                "top_hit_count": len(top_hits),
                "slot_count": len((slot_schema or {}).get("slots") or []),
            },
            lambda: self.answer_arbitration.run(item, query_text, top_hits, slot_schema=slot_schema),
        )

    def run_overlay_control(self, item: dict[str, Any], generated: dict[str, Any], prediction: Any, top_hits: list[dict[str, Any]]) -> Any:
        return self.runtime.run_role(
            self.overlay_control.name,
            {
                **_role_payload(item, stage="overlay_control"),
                "answer_status": generated.get("answer_status"),
                "source_chunk_id_count": len(generated.get("source_chunk_ids") or []),
                "top_hit_count": len(top_hits),
            },
            lambda: self.overlay_control.run(item, generated, prediction, top_hits),
        )

    def process_item(self, item: dict[str, Any]) -> Any:
        from nested_doc_rag.agent.step15_runner import Step15FieldResult, field_id_for_item

        field_id = field_id_for_item(item)
        self.trace.record(field_id, "controller", "field_started", {"mode": self.mode, "agentscope_available": self.runtime.available})

        query_plan = self.run_query_planner(item)
        self.trace.record(
            field_id,
            self.query_planner.name,
            "query_planned",
            {"base_query": query_plan.base_query, "query_text": query_plan.query_text},
        )

        retrieval = self.run_evidence_retrieval(item, query_plan.query_text)
        self.trace.record(
            field_id,
            self.evidence_retrieval.name,
            "evidence_retrieved",
            {
                "top_hit_count": len(retrieval.top_hits),
                "vector_hit_count": len(retrieval.vector_hits),
                "retrieval_latency_ms": retrieval.retrieval_latency_ms,
            },
        )

        arbitration = self.run_answer_arbitration(item, query_plan.query_text, retrieval.top_hits)
        self.trace.record(
            field_id,
            self.answer_arbitration.name,
            "answer_arbitrated",
            {
                "answer_status": arbitration.generated.get("answer_status"),
                "generation_latency_ms": arbitration.generation_latency_ms,
            },
        )

        overlay = self.run_overlay_control(item, arbitration.generated, arbitration.prediction, retrieval.top_hits)
        self.trace.record(
            field_id,
            self.overlay_control.name,
            "overlay_controlled",
            {
                "critic_flags": overlay.critic_flags,
                "review_required": overlay.overlay.review_required,
                "writeback_allowed": overlay.overlay.writeback_allowed,
            },
        )

        return Step15FieldResult(
            item=item,
            masked_query=query_plan.query_text,
            prediction=arbitration.prediction,
            generated=arbitration.generated,
            top_hits=retrieval.top_hits,
            vector_hits=retrieval.vector_hits,
            overlay=overlay.overlay,
            review_item=overlay.review_item,
            eval_result=None,
            retrieval_latency_ms=retrieval.retrieval_latency_ms,
            generation_latency_ms=arbitration.generation_latency_ms,
            critic_flags=overlay.critic_flags,
        )

    def write_optional_artifacts(self, out_dir: Path) -> None:
        if self.mode not in {"equivalent_mas", "trace_only"}:
            return
        self.trace.write_jsonl(out_dir / "mas_trace.jsonl")
        write_jsonl(
            out_dir / "agentscope_events.jsonl",
            [
                {
                    "event_type": "runtime_selected",
                    "mode": self.mode,
                    "agentscope_available": self.runtime.available,
                    "agentscope_version": self.runtime.agentscope_version,
                    "fallback_reason": self.runtime.reason,
                }
            ]
            + self.runtime.events,
        )

    def record_trace_only_result(self, item: dict[str, Any], result: Any) -> None:
        from nested_doc_rag.agent.step15_runner import field_id_for_item

        field_id = field_id_for_item(item)
        self.trace.record(
            field_id,
            "controller",
            "trace_only_observed",
            {
                "answer_status": result.prediction.answer_status,
                "critic_flags": result.critic_flags,
                "review_required": result.overlay.review_required,
                "writeback_allowed": result.overlay.writeback_allowed,
            },
        )


def _role_payload(item: dict[str, Any], *, stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "field_id": item.get("form_item_id"),
        "row_index": item.get("row_index"),
        "target_cell": item.get("target_cell"),
    }
