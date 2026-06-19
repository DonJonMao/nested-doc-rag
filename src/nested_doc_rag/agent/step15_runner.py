from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Literal
from uuid import uuid4

from nested_doc_rag.config import AppConfig
from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.evaluation.step15_engine import (
    Step15RetrievalResult,
    add_room_context,
    all_base_cloud_rows,
    build_qdrant_answer_messages,
    run_step15_retrieval,
)
from nested_doc_rag.excel.writeback import patch_workbook
from nested_doc_rag.gongkan_eval import build_judge_messages, build_masked_query, call_deepseek_json
from nested_doc_rag.grounding import EvidenceStrengthEvaluator, EvidenceStrengthResult, apply_evidence_strength_to_overlay
from nested_doc_rag.io import display_text, read_jsonl, write_json, write_jsonl
from nested_doc_rag.llm import JsonRepairError
from nested_doc_rag.retrieval import QdrantRetriever
from nested_doc_rag.schemas.eval import FieldPrediction

from .mas.controller import Step15MASController

AnswerCaller = Callable[..., dict[str, Any]]
JudgeCaller = Callable[..., dict[str, Any]]
RetrievalFn = Callable[[str], Step15RetrievalResult]
WritebackFn = Callable[..., Any]

ANSWER_STATUSES = {"answered", "partial_clue", "not_found", "conflict_unresolved"}
PROMPT_VERSIONS = {"step15_compat", "agent_v2"}
UNSAFE_WRITEBACK_FLAGS = {
    "answered_without_source",
    "invalid_source_reference",
    "answered_from_global_intro_risk",
    "answer_too_long",
    "scope_mismatch_risk",
    "liquid_cooling_scope_mismatch",
    "field_intent_source_mismatch",
}
RISKY_ANSWERED_DOWNGRADE_FLAGS = {
    "answered_without_source",
    "invalid_source_reference",
    "answered_from_global_intro_risk",
    "scope_mismatch_risk",
    "liquid_cooling_scope_mismatch",
    "field_intent_source_mismatch",
}
CRITICAL_OVERLAY_FLAGS = RISKY_ANSWERED_DOWNGRADE_FLAGS | {"answer_too_long"}


@dataclass(frozen=True)
class AgentOverlay:
    field_id: str
    row_index: int | None
    target_cell: str | None
    critic_flags: list[str]
    review_required: bool
    writeback_allowed: bool
    suggested_status: str | None
    suggested_answer_value: str | None
    suggested_reference_source_documents: list[dict[str, Any]]
    suggested_reference_chunk_ids: list[str]
    suggested_reference_snippets: list[str]
    risk_level: str
    reasons: list[str]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentOverlay:
        return cls(
            field_id=str(value["field_id"]),
            row_index=int(value["row_index"]) if value.get("row_index") is not None else None,
            target_cell=value.get("target_cell"),
            critic_flags=[str(item) for item in value.get("critic_flags") or []],
            review_required=bool(value.get("review_required")),
            writeback_allowed=bool(value.get("writeback_allowed")),
            suggested_status=str(value["suggested_status"]) if value.get("suggested_status") is not None else None,
            suggested_answer_value=str(value["suggested_answer_value"]) if value.get("suggested_answer_value") is not None else None,
            suggested_reference_source_documents=[
                dict(item) for item in value.get("suggested_reference_source_documents") or [] if isinstance(item, dict)
            ],
            suggested_reference_chunk_ids=[str(item) for item in value.get("suggested_reference_chunk_ids") or []],
            suggested_reference_snippets=[str(item) for item in value.get("suggested_reference_snippets") or []],
            risk_level=str(value.get("risk_level") or "low"),
            reasons=[str(item) for item in value.get("reasons") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "row_index": self.row_index,
            "target_cell": self.target_cell,
            "critic_flags": self.critic_flags,
            "review_required": self.review_required,
            "writeback_allowed": self.writeback_allowed,
            "suggested_status": self.suggested_status,
            "suggested_answer_value": self.suggested_answer_value,
            "suggested_reference_source_documents": self.suggested_reference_source_documents,
            "suggested_reference_chunk_ids": self.suggested_reference_chunk_ids,
            "suggested_reference_snippets": self.suggested_reference_snippets,
            "risk_level": self.risk_level,
            "reasons": self.reasons,
        }


@dataclass
class Step15FieldResult:
    item: dict[str, Any]
    masked_query: str
    prediction: FieldPrediction
    generated: dict[str, Any]
    top_hits: list[dict[str, Any]]
    vector_hits: list[dict[str, Any]]
    overlay: AgentOverlay
    review_item: dict[str, Any] | None
    eval_result: dict[str, Any] | None
    retrieval_latency_ms: float
    generation_latency_ms: float
    critic_flags: list[str]


class Step15AgentRunner:
    def __init__(
        self,
        *,
        config: AppConfig,
        target_namespace: str,
        out_dir: Path,
        global_namespace: str = "global",
        room_context: str | None = None,
        retrieval_plan: Literal["layered"] = "layered",
        vector_top_k: int | None = None,
        rerank_top_n: int | None = None,
        judge_enabled: bool = False,
        writeback_enabled: bool = False,
        template_path: Path | None = None,
        checkpoint_every: int = 1,
        resume: bool = False,
        timeout_seconds: int | None = None,
        chat_max_retries: int = 2,
        chat_retry_backoff_seconds: int = 3,
        prompt_version: Literal["step15_compat", "agent_v2"] = "step15_compat",
        judge_cache_path: Path | None = None,
        use_judge_cache: bool = False,
        deepseek_api_key_env: str | None = None,
        qdrant_path: Path | None = None,
        collection_name: str | None = None,
        embedding_endpoint: str | None = None,
        embedding_model: str | None = None,
        rerank_endpoint: str | None = None,
        rerank_model: str | None = None,
        chat_endpoint: str | None = None,
        chat_model: str | None = None,
        chat_api_key: str | None = None,
        allowed_layers: list[str] | None = None,
        layered_plan: list[dict[str, Any]] | None = None,
        grounding_enabled: bool | None = None,
        retriever: QdrantRetriever | None = None,
        reranker: RerankClient | None = None,
        retrieval_fn: RetrievalFn | None = None,
        answer_caller: AnswerCaller | None = None,
        judge_caller: JudgeCaller | None = None,
        writeback_fn: WritebackFn = patch_workbook,
    ) -> None:
        self.config = config
        self.target_namespace = target_namespace
        self.global_namespace = global_namespace
        self.room_context = room_context
        self.out_dir = out_dir
        if retrieval_plan != "layered":
            raise ValueError("Step15 production path supports layered retrieval only")
        self.retrieval_plan = "layered"
        self.retrieval_mode = self.retrieval_plan
        self.vector_top_k = vector_top_k or config.retrieval.vector_top_k
        self.rerank_top_n = rerank_top_n or config.retrieval.rerank_top_n
        self.judge_enabled = judge_enabled
        self.writeback_enabled = writeback_enabled
        self.template_path = template_path
        self.checkpoint_every = max(1, checkpoint_every)
        self.resume = resume
        self.timeout_seconds = timeout_seconds or config.services.timeout_seconds
        self.chat_max_retries = max(0, chat_max_retries)
        self.chat_retry_backoff_seconds = max(0, chat_retry_backoff_seconds)
        if prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"unsupported prompt_version: {prompt_version}")
        self.prompt_version = prompt_version
        self.judge_cache_path = judge_cache_path
        self.use_judge_cache = use_judge_cache
        self.judge_cache: dict[str, dict[str, Any]] = load_judge_cache(judge_cache_path) if use_judge_cache and judge_cache_path else {}
        self.deepseek_api_key_env = deepseek_api_key_env or config.services.chat_api_key_env
        self.qdrant_path = qdrant_path or config.paths.qdrant_path
        self.collection_name = collection_name or config.qdrant.collection_name
        self.embedding_endpoint = embedding_endpoint or config.services.embedding_endpoint
        self.embedding_model = embedding_model or config.services.embedding_model
        self.rerank_endpoint = rerank_endpoint or config.services.rerank_endpoint
        self.rerank_model = rerank_model if rerank_model is not None else config.services.rerank_model
        self.chat_endpoint = chat_endpoint or config.services.chat_endpoint
        self.chat_model = chat_model or config.services.chat_model
        self.chat_api_key = chat_api_key if chat_api_key is not None else os.environ.get(self.deepseek_api_key_env, "")
        self.allowed_layers = allowed_layers or config.retrieval.query_layers
        self.layered_plan = layered_plan or config.retrieval.layered_plan
        self.grounding_enabled = (
            bool(config.grounding.evidence_strength_enabled) if grounding_enabled is None else bool(grounding_enabled)
        )
        self.answer_caller = answer_caller
        self.judge_caller = judge_caller
        self.writeback_fn = writeback_fn
        self.retrieval_fn = retrieval_fn
        self.run_id = f"step15_agent_{uuid4().hex[:12]}"
        self.trace = TraceRecorderShim(
            run_id=self.run_id,
            metadata={
                "engine": "step15_agent",
                "target_namespace": self.target_namespace,
                "global_namespace": self.global_namespace,
                "retrieval_plan": self.retrieval_plan,
                "collection_name": self.collection_name,
                "chat_model": self.chat_model,
                "prompt_version": self.prompt_version,
                "use_judge_cache": self.use_judge_cache,
                "judge_enabled": self.judge_enabled,
                "writeback_enabled": self.writeback_enabled,
            },
        )
        self.review_items: list[dict[str, Any]] = []
        self.eval_results: list[dict[str, Any]] = []
        self.agent_overlays: list[AgentOverlay] = []
        self.grounding_trace_records: list[dict[str, Any]] = []
        self.writeback_status = "skipped: writeback disabled"
        self.writeback_summary: dict[str, Any] | None = None
        self.mas_mode = config.agentscope.mode if config.agentscope.enabled or config.agentscope.mode != "off" else "off"
        if self.mas_mode not in {"off", "equivalent_mas", "trace_only"}:
            raise ValueError(f"unsupported agentscope.mode: {self.mas_mode}")
        self.mas_controller = (
            Step15MASController(self, mode=self.mas_mode, agentscope_enabled=config.agentscope.enabled)
            if self.mas_mode in {"equivalent_mas", "trace_only"}
            else None
        )
        self._owns_retriever = retriever is None and retrieval_fn is None
        self.retriever = retriever
        self.reranker = reranker
        if retrieval_fn is None:
            self.retriever = self.retriever or QdrantRetriever(
                qdrant_path=self.qdrant_path,
                collection_name=self.collection_name,
                embedding_endpoint=self.embedding_endpoint,
                embedding_model=self.embedding_model,
                prefer_grpc=config.qdrant.prefer_grpc,
                timeout=config.qdrant.timeout,
            )
            self.reranker = self.reranker or RerankClient(
                endpoint=self.rerank_endpoint,
                model=self.rerank_model,
                timeout_seconds=self.timeout_seconds,
            )
    def run(self, items: list[dict[str, Any]]) -> list[FieldPrediction]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_predictions = self.load_checkpoint_predictions() if self.resume else {}
        completed_keys = completed_item_keys(checkpoint_predictions.values())
        if self.resume:
            self.load_checkpoint_sidecars()

        skipped_completed_count = sum(1 for item in items if item_key(item) in completed_keys)
        run_state: dict[str, Any] = {
            "run_id": self.run_id,
            "engine": "step15_agent",
            "target_namespace": self.target_namespace,
            "global_namespace": self.global_namespace,
            "room_context": display_text(self.room_context),
            "rows": rows_label_for_items(items),
            "retrieval_plan": self.retrieval_plan,
            "retrieval_fusion_mode": "dense",
            "fields_total": len(items),
            "fields_completed": skipped_completed_count,
            "fields_failed": 0,
            "resumed_count": 1 if self.resume and checkpoint_predictions else 0,
            "skipped_completed_count": skipped_completed_count,
            "judge_enabled": self.judge_enabled,
            "writeback_enabled": self.writeback_enabled,
            "started_at": now_iso(),
            "finished_at": "",
        }
        self.trace.record(None, "run_started", self.run_metadata())
        if self.resume and checkpoint_predictions:
            self.trace.record(
                None,
                "resume_started",
                {
                    "prediction_checkpoint": str(self.predictions_checkpoint_path()),
                    "skipped_completed_count": skipped_completed_count,
                    "completed_rows": sorted(row for row in completed_keys if row.startswith("row:")),
                },
            )

        predictions_by_field_id: dict[str, FieldPrediction] = dict(checkpoint_predictions)
        overlays_by_field_id: dict[str, AgentOverlay] = {overlay.field_id: overlay for overlay in self.agent_overlays}
        processed_since_checkpoint = 0
        try:
            for item in items:
                key = item_key(item)
                if key in completed_keys:
                    continue
                field_id = field_id_for_item(item)
                try:
                    result = self.process_item(item)
                except Exception as exc:  # noqa: BLE001 - one failed field must not abort a long run
                    run_state["fields_failed"] += 1
                    result = self.failed_item_result(item, exc)

                predictions_by_field_id[result.prediction.field_id] = result.prediction
                overlays_by_field_id[result.overlay.field_id] = result.overlay
                if result.eval_result is not None:
                    self.eval_results.append(result.eval_result)
                if result.review_item is not None:
                    self.review_items.append(result.review_item)
                run_state["fields_completed"] += 1
                processed_since_checkpoint += 1
                self.trace.record(
                    field_id,
                    "field_completed",
                    {
                        "raw_status": result.prediction.answer_status,
                        "overlay_suggested_status": result.overlay.suggested_status,
                        "raw_prediction": result.prediction.to_dict(),
                        "agent_overlay": result.overlay.to_dict(),
                        "needs_review": result.review_item is not None,
                        "critic_flags": result.critic_flags,
                    },
                )
                self.trace.record(
                    field_id,
                    "checkpoint_written",
                    {
                        "prediction_checkpoint": str(self.predictions_checkpoint_path()),
                        "checkpoint_every": self.checkpoint_every,
                    },
                )
                completed_keys.add(key)
                if processed_since_checkpoint >= self.checkpoint_every:
                    self.write_checkpoint(items, predictions_by_field_id, overlays_by_field_id, run_state)
                    processed_since_checkpoint = 0
        finally:
            if self._owns_retriever and self.retriever is not None:
                self.retriever.close()

        run_state["finished_at"] = now_iso()
        self.trace.record(None, "run_completed", run_state)
        ordered_predictions = ordered_predictions_for_items(items, predictions_by_field_id)
        ordered_overlays = ordered_overlays_for_predictions(ordered_predictions, overlays_by_field_id)
        self.write_checkpoint(items, predictions_by_field_id, overlays_by_field_id, run_state)
        self.write_outputs(ordered_predictions, ordered_overlays, run_state)
        return ordered_predictions

    def process_item(self, item: dict[str, Any]) -> Step15FieldResult:
        if self.mas_mode == "equivalent_mas" and self.mas_controller is not None:
            return self._process_item_equivalent_mas(item)
        result = self._process_item_original(item)
        if self.mas_mode == "trace_only" and self.mas_controller is not None:
            self.mas_controller.record_trace_only_result(item, result)
        return result

    def _process_item_original(self, item: dict[str, Any]) -> Step15FieldResult:
        field_id = field_id_for_item(item)
        self.trace.record(field_id, "field_started", {"field": minimal_item_view(item), "room_context": display_text(self.room_context)})

        base_query = build_masked_query(item, self.target_namespace)
        query_text = add_room_context(base_query, self.room_context)
        self.trace.record(
            field_id,
            "query_planned",
            {
                "masked_query_preview": display_text(query_text, 240),
                "masked_query": query_text,
                "room_context": display_text(self.room_context),
            },
        )

        retrieval_started = perf_counter_ms()
        retrieval_result = self.retrieve(query_text)
        retrieval_latency_ms = round(perf_counter_ms() - retrieval_started, 3)
        top_hits = retrieval_result.reranked_hits
        vector_hits = retrieval_result.vector_hits
        self.trace.record(
            field_id,
            "layered_retrieval_finished",
            {
                "retrieval_plan": self.retrieval_plan,
                "total_hits": len(top_hits),
                "vector_hit_count": len(vector_hits),
                "layer_counts": count_layers(top_hits),
                "retrieval_latency_ms": retrieval_latency_ms,
                "top_chunk_ids": chunk_ids(top_hits),
            },
        )

        generation_started = perf_counter_ms()
        messages = build_qdrant_answer_messages(item, query_text, top_hits, room_context=self.room_context, prompt_version=self.prompt_version)
        generated = self.call_answer(messages=messages, item=item, query_text=query_text, hits=top_hits)
        generation_latency_ms = round(perf_counter_ms() - generation_started, 3)
        self.trace.record(
            field_id,
            "answer_arbitrated",
            {
                "chat_model": self.chat_model,
                "prompt_version": self.prompt_version,
                "answer_status": generated.get("answer_status"),
                "source_chunk_ids": generated.get("source_chunk_ids") or [],
                "reference_source_documents_count": len(generated.get("reference_source_documents") or []),
                "generation_latency_ms": generation_latency_ms,
            },
        )

        prediction = convert_step15_generated_to_prediction(item, generated, top_hits, retrieval_mode=self.retrieval_plan)
        critic_flags = critic_check_step15_answer(item, generated, top_hits)
        overlay = build_agent_overlay_for_step15_prediction(prediction, top_hits, critic_flags)
        grounding_result: EvidenceStrengthResult | None = None
        if self.grounding_enabled:
            grounding_result = EvidenceStrengthEvaluator(
                target_namespace=self.target_namespace,
                global_intro_answer_allowed=self.config.grounding.global_intro_answer_allowed,
                require_target_source_for_answered=self.config.grounding.require_target_source_for_answered,
                room_context=self.room_context,
            ).evaluate(item=item, prediction=prediction, top_hits=top_hits)
            overlay = apply_evidence_strength_to_overlay(
                prediction,
                overlay,
                grounding_result,
                min_strength_for_answered=self.config.grounding.min_strength_for_answered,
                min_strength_for_writeback=self.config.grounding.min_strength_for_writeback,
                downgrade_unsupported_answer_to_partial=self.config.grounding.downgrade_unsupported_answer_to_partial,
            )
            self.trace.record(field_id, "grounding_evaluated", grounding_result.to_dict())
            if self.config.grounding.write_grounding_trace:
                self.grounding_trace_records.append(
                    {
                        "field_id": field_id,
                        "query_text": query_text,
                        "retrieval_plan": self.retrieval_plan,
                        "answer_status": prediction.answer_status,
                        "answer_value": prediction.answer_value,
                        "source_chunk_ids": prediction.source_chunk_ids,
                        **grounding_result.to_dict(),
                        "overlay": {
                            "review_required": overlay.review_required,
                            "writeback_allowed": overlay.writeback_allowed,
                            "risk_level": overlay.risk_level,
                            "reasons": overlay.reasons,
                        },
                    }
                )
        self.trace.record(
            field_id,
            "agent_overlay_built",
            {
                "raw_status": prediction.answer_status,
                "suggested_status": overlay.suggested_status,
                "review_required": overlay.review_required,
                "writeback_allowed": overlay.writeback_allowed,
                "risk_level": overlay.risk_level,
                "reasons": overlay.reasons,
                "critic_flags": critic_flags,
                "evidence_strength": grounding_result.evidence_strength if grounding_result else None,
                "field_binding": grounding_result.field_binding if grounding_result else None,
                "suggested_reference_source_documents_count": len(overlay.suggested_reference_source_documents),
            },
        )
        self.trace.record(
            field_id,
            "prediction_normalized",
            {"raw_prediction": prediction.to_dict(), "source_ids_valid": prediction.validation.get("source_ids_valid")},
        )
        self.trace.record(field_id, "critic_checked", {"critic_flags": critic_flags})

        review_item = make_step15_review_item(item, prediction, overlay, top_hits)
        self.trace.record(
            field_id,
            "review_routed",
            {
                "needs_review": review_item is not None,
                "suggested_action": (review_item or {}).get("suggested_action"),
                "critic_flags": critic_flags,
                "overlay": overlay.to_dict(),
            },
        )

        eval_result = None
        if self.judge_enabled:
            heldout_answer = str(item.get("existing_value") or item.get("heldout_answer") or "")
            judge = self.get_or_call_judge(
                item=item,
                generated=generated,
                heldout_answer=heldout_answer,
            )
            eval_result = make_eval_result(item, generated, judge, top_hits, vector_hits, query_text, self.room_context)
            self.trace.record(
                field_id,
                "judge_completed",
                {"label": judge.get("label"), "score": judge.get("score"), "reason": judge.get("reason")},
            )

        return Step15FieldResult(
            item=item,
            masked_query=query_text,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            vector_hits=vector_hits,
            overlay=overlay,
            review_item=review_item,
            eval_result=eval_result,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            critic_flags=critic_flags,
        )

    def _process_item_equivalent_mas(self, item: dict[str, Any]) -> Step15FieldResult:
        if self.mas_controller is None:
            return self._process_item_original(item)
        field_id = field_id_for_item(item)
        self.trace.record(field_id, "field_started", {"field": minimal_item_view(item), "room_context": display_text(self.room_context)})

        query_plan = self.mas_controller.run_query_planner(item)
        self.mas_controller.trace.record(
            field_id,
            self.mas_controller.query_planner.name,
            "query_planned",
            {"base_query": query_plan.base_query, "query_text": query_plan.query_text},
        )
        query_text = query_plan.query_text
        self.trace.record(
            field_id,
            "query_planned",
            {
                "masked_query_preview": display_text(query_text, 240),
                "masked_query": query_text,
                "room_context": display_text(self.room_context),
            },
        )

        retrieval = self.mas_controller.run_evidence_retrieval(item, query_text)
        top_hits = retrieval.top_hits
        vector_hits = retrieval.vector_hits
        self.mas_controller.trace.record(
            field_id,
            self.mas_controller.evidence_retrieval.name,
            "evidence_retrieved",
            {
                "top_hit_count": len(top_hits),
                "vector_hit_count": len(vector_hits),
                "retrieval_latency_ms": retrieval.retrieval_latency_ms,
            },
        )
        self.trace.record(
            field_id,
            "layered_retrieval_finished",
            {
                "retrieval_plan": self.retrieval_plan,
                "total_hits": len(top_hits),
                "vector_hit_count": len(vector_hits),
                "layer_counts": count_layers(top_hits),
                "retrieval_latency_ms": retrieval.retrieval_latency_ms,
                "top_chunk_ids": chunk_ids(top_hits),
            },
        )

        arbitration = self.mas_controller.run_answer_arbitration(item, query_text, top_hits)
        generated = arbitration.generated
        prediction = arbitration.prediction
        self.mas_controller.trace.record(
            field_id,
            self.mas_controller.answer_arbitration.name,
            "answer_arbitrated",
            {"answer_status": generated.get("answer_status"), "generation_latency_ms": arbitration.generation_latency_ms},
        )
        self.trace.record(
            field_id,
            "answer_arbitrated",
            {
                "chat_model": self.chat_model,
                "prompt_version": self.prompt_version,
                "answer_status": generated.get("answer_status"),
                "source_chunk_ids": generated.get("source_chunk_ids") or [],
                "reference_source_documents_count": len(generated.get("reference_source_documents") or []),
                "generation_latency_ms": arbitration.generation_latency_ms,
            },
        )

        overlay_control = self.mas_controller.run_overlay_control(item, generated, prediction, top_hits)
        critic_flags = overlay_control.critic_flags
        overlay = overlay_control.overlay
        review_item = overlay_control.review_item
        self.mas_controller.trace.record(
            field_id,
            self.mas_controller.overlay_control.name,
            "overlay_controlled",
            {
                "critic_flags": critic_flags,
                "review_required": overlay.review_required,
                "writeback_allowed": overlay.writeback_allowed,
            },
        )
        self.trace.record(
            field_id,
            "agent_overlay_built",
            {
                "raw_status": prediction.answer_status,
                "suggested_status": overlay.suggested_status,
                "review_required": overlay.review_required,
                "writeback_allowed": overlay.writeback_allowed,
                "risk_level": overlay.risk_level,
                "reasons": overlay.reasons,
                "critic_flags": critic_flags,
                "suggested_reference_source_documents_count": len(overlay.suggested_reference_source_documents),
            },
        )
        self.trace.record(
            field_id,
            "prediction_normalized",
            {"raw_prediction": prediction.to_dict(), "source_ids_valid": prediction.validation.get("source_ids_valid")},
        )
        self.trace.record(field_id, "critic_checked", {"critic_flags": critic_flags})

        self.trace.record(
            field_id,
            "review_routed",
            {
                "needs_review": review_item is not None,
                "suggested_action": (review_item or {}).get("suggested_action"),
                "critic_flags": critic_flags,
                "overlay": overlay.to_dict(),
            },
        )

        eval_result = None
        if self.judge_enabled:
            heldout_answer = str(item.get("existing_value") or item.get("heldout_answer") or "")
            judge = self.get_or_call_judge(
                item=item,
                generated=generated,
                heldout_answer=heldout_answer,
            )
            eval_result = make_eval_result(item, generated, judge, top_hits, vector_hits, query_text, self.room_context)
            self.trace.record(
                field_id,
                "judge_completed",
                {"label": judge.get("label"), "score": judge.get("score"), "reason": judge.get("reason")},
            )

        return Step15FieldResult(
            item=item,
            masked_query=query_text,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            vector_hits=vector_hits,
            overlay=overlay,
            review_item=review_item,
            eval_result=eval_result,
            retrieval_latency_ms=retrieval.retrieval_latency_ms,
            generation_latency_ms=arbitration.generation_latency_ms,
            critic_flags=critic_flags,
        )

    def failed_item_result(self, item: dict[str, Any], exc: Exception) -> Step15FieldResult:
        field_id = field_id_for_item(item)
        prediction = FieldPrediction(
            field_id=field_id,
            row_index=int(item.get("row_index") or 0),
            target_cell=item.get("target_cell"),
            answer_value="处理失败，请人工复核",
            answer_status="conflict_unresolved",
            confidence=0.0,
            source_chunk_ids=[],
            evidence_attachment_ids=[],
            validation={
                "engine": "step15_agent",
                "error": str(exc),
                "failed_step": "process_item",
                "needs_human_review": True,
                "validation_pass": False,
            },
            method_name="step15_agent_failed",
        )
        overlay = AgentOverlay(
            field_id=field_id,
            row_index=prediction.row_index,
            target_cell=prediction.target_cell,
            critic_flags=["field_failed"],
            review_required=True,
            writeback_allowed=False,
            suggested_status="conflict_unresolved",
            suggested_answer_value="处理失败，请人工复核",
            suggested_reference_source_documents=[],
            suggested_reference_chunk_ids=[],
            suggested_reference_snippets=[],
            risk_level="high",
            reasons=["field_failed"],
        )
        review_item = make_step15_review_item(item, prediction, overlay, [])
        generated = {
            "answer_value": prediction.answer_value,
            "answer_status": prediction.answer_status,
            "confidence": 0.0,
            "source_chunk_ids": [],
            "reference_source_documents": [],
            "reason": str(exc),
        }
        eval_result = None
        if self.judge_enabled:
            eval_result = make_eval_result(
                item,
                generated,
                {"label": "mismatch", "score": 0, "reason": f"field failed: {exc}"},
                [],
                [],
                "",
                self.room_context,
            )
        self.trace.record(field_id, "field_failed", {"error": str(exc), "final_prediction": prediction.to_dict()})
        self.trace.record(field_id, "review_routed", {"needs_review": True, "critic_flags": ["field_failed"], "overlay": overlay.to_dict()})
        return Step15FieldResult(
            item=item,
            masked_query="",
            prediction=prediction,
            generated=generated,
            top_hits=[],
            vector_hits=[],
            overlay=overlay,
            review_item=review_item,
            eval_result=eval_result,
            retrieval_latency_ms=0.0,
            generation_latency_ms=0.0,
            critic_flags=["field_failed"],
        )

    def retrieve(self, query_text: str) -> Step15RetrievalResult:
        if self.retrieval_fn is not None:
            return self.retrieval_fn(query_text)
        if self.retriever is None or self.reranker is None:
            raise RuntimeError("Step15AgentRunner requires retriever and reranker")
        return run_step15_retrieval(
            query_text,
            retriever=self.retriever,
            reranker=self.reranker,
            target_namespace=self.target_namespace,
            global_namespace=self.global_namespace,
            allowed_layers=self.allowed_layers,
            retrieval_mode=self.retrieval_plan,
            vector_top_k=self.vector_top_k,
            rerank_top_n=self.rerank_top_n,
            layered_plan=self.layered_plan,
        )

    def call_answer(self, **kwargs: Any) -> dict[str, Any]:
        return self.call_chat_with_retries(
            call_kind="answer",
            field_id=field_id_for_item(kwargs.get("item") or {}),
            caller=self.answer_caller,
            kwargs=kwargs,
        )

    def call_judge(self, **kwargs: Any) -> dict[str, Any]:
        return self.call_chat_with_retries(
            call_kind="judge",
            field_id=field_id_for_item(kwargs.get("item") or {}),
            caller=self.judge_caller,
            kwargs=kwargs,
        )

    def get_or_call_judge(self, *, item: dict[str, Any], generated: dict[str, Any], heldout_answer: str) -> dict[str, Any]:
        messages = build_judge_messages(item, generated, heldout_answer)
        cache_key = build_judge_cache_key(
            item=item,
            generated=generated,
            heldout_answer=heldout_answer,
            judge_prompt_version="gongkan_eval_v1",
            judge_model=self.chat_model,
        )
        field_id = field_id_for_item(item)
        if self.use_judge_cache and cache_key in self.judge_cache:
            judge = dict(self.judge_cache[cache_key])
            self.trace.record(field_id, "judge_cache_hit", {"cache_key": cache_key, "label": judge.get("label"), "score": judge.get("score")})
            return judge

        judge = self.call_judge(messages=messages, item=item, generated=generated, heldout_answer=heldout_answer)
        if self.use_judge_cache and self.judge_cache_path is not None:
            self.judge_cache[cache_key] = dict(judge)
            append_judge_cache_record(
                self.judge_cache_path,
                {
                    "cache_key": cache_key,
                    "judge": judge,
                    "metadata": {
                        "field_id": field_id,
                        "row_index": item.get("row_index"),
                        "judge_prompt_version": "gongkan_eval_v1",
                        "judge_model": self.chat_model,
                    },
                },
            )
            self.trace.record(field_id, "judge_cache_written", {"cache_key": cache_key, "label": judge.get("label"), "score": judge.get("score")})
        return judge

    def call_chat_with_retries(
        self,
        *,
        call_kind: str,
        field_id: str,
        caller: AnswerCaller | JudgeCaller | None,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        attempts = self.chat_max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                if caller is not None:
                    result = caller(**kwargs)
                else:
                    result = call_deepseek_json(
                        url=self.chat_endpoint,
                        model=self.chat_model,
                        api_key=self.chat_api_key,
                        messages=kwargs["messages"],
                        timeout=self.timeout_seconds,
                    )
            except Exception as exc:  # noqa: BLE001 - retry wraps fake and real chat callers
                if is_json_parse_error(exc):
                    self.trace.record(
                        field_id,
                        "json_parse_failed",
                        {
                            "call_kind": call_kind,
                            "attempt": attempt,
                            "error": display_text(str(exc), 240),
                        },
                    )
                retryable = is_retryable_chat_error(exc)
                if not retryable or attempt >= attempts:
                    if retryable:
                        self.trace.record(
                            field_id,
                            "chat_retry_failed",
                            {
                                "call_kind": call_kind,
                                "attempt": attempt,
                                "max_retries": self.chat_max_retries,
                                "error": display_text(str(exc), 240),
                            },
                        )
                    raise
                self.trace.record(
                    field_id,
                    "chat_retry_started",
                    {
                        "call_kind": call_kind,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "max_retries": self.chat_max_retries,
                        "backoff_seconds": self.chat_retry_backoff_seconds,
                        "error": display_text(str(exc), 240),
                    },
                )
                if self.chat_retry_backoff_seconds:
                    sleep(self.chat_retry_backoff_seconds)
                continue
            if attempt > 1:
                self.trace.record(
                    field_id,
                    "chat_retry_succeeded",
                    {"call_kind": call_kind, "attempt": attempt, "max_retries": self.chat_max_retries},
                )
            return result
        raise RuntimeError("chat retry loop exited unexpectedly")

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
        if self.review_checkpoint_path().exists():
            self.review_items = read_jsonl(self.review_checkpoint_path())
        if self.eval_checkpoint_path().exists():
            self.eval_results = read_jsonl(self.eval_checkpoint_path())
        if self.overlay_checkpoint_path().exists():
            self.agent_overlays = [AgentOverlay.from_dict(record) for record in read_jsonl(self.overlay_checkpoint_path())]
        self.trace.load_jsonl(self.trace_checkpoint_path())

    def write_checkpoint(
        self,
        items: list[dict[str, Any]],
        predictions_by_field_id: dict[str, FieldPrediction],
        overlays_by_field_id: dict[str, AgentOverlay],
        run_state: dict[str, Any],
    ) -> None:
        predictions = ordered_predictions_for_items(items, predictions_by_field_id)
        overlays = ordered_overlays_for_predictions(predictions, overlays_by_field_id)
        write_jsonl(self.predictions_checkpoint_path(), [prediction.to_dict() for prediction in predictions])
        write_jsonl(self.overlay_checkpoint_path(), [overlay.to_dict() for overlay in overlays])
        if self.judge_enabled:
            write_jsonl(self.eval_checkpoint_path(), ordered_eval_results_for_items(items, self.eval_results))
        write_jsonl(self.review_checkpoint_path(), self.review_items)
        self.trace.write_jsonl(self.trace_checkpoint_path())
        write_json(self.out_dir / "run_state.json", run_state)

    def write_outputs(self, predictions: list[FieldPrediction], overlays: list[AgentOverlay], run_state: dict[str, Any]) -> None:
        predictions_path = self.out_dir / "predictions.jsonl"
        trace_path = self.out_dir / "trace.jsonl"
        review_items_path = self.out_dir / "review_items.jsonl"
        write_jsonl(predictions_path, [prediction.to_dict() for prediction in predictions])
        write_jsonl(self.out_dir / "predictions_raw.jsonl", [prediction.to_dict() for prediction in predictions])
        write_jsonl(self.out_dir / "agent_overlays.jsonl", [overlay.to_dict() for overlay in overlays])
        write_jsonl(self.out_dir / "predictions_agent_view.jsonl", build_agent_view_records(predictions, overlays))
        if self.judge_enabled:
            write_jsonl(self.out_dir / "eval_results.jsonl", ordered_eval_results_for_items_by_predictions(predictions, self.eval_results))
        write_jsonl(review_items_path, self.review_items)
        self.trace.write_jsonl(trace_path)
        trace_summary = build_trace_summary(self.trace.events, predictions, self.review_items, overlays)
        write_json(self.out_dir / "trace_summary.json", trace_summary)
        self.trace.write_markdown(self.out_dir / "trace.md", trace_summary)
        self.maybe_writeback(predictions, overlays)
        if self.mas_controller is not None:
            self.mas_controller.write_optional_artifacts(self.out_dir)
        if self.config.grounding.write_grounding_trace and self.grounding_trace_records:
            write_jsonl(self.out_dir / "grounding_trace.jsonl", self.grounding_trace_records)
        output_files = sorted({path.name for path in self.out_dir.iterdir() if path.is_file()} | {"run_summary.md", "summary.json", "run_manifest.json"})
        summary = build_summary_json(
            predictions=predictions,
            overlays=overlays,
            eval_results=self.eval_results if self.judge_enabled else [],
            trace_summary=trace_summary,
            run_state=run_state,
            writeback_status=self.writeback_status,
            writeback_summary=self.writeback_summary,
        )
        write_json(self.out_dir / "summary.json", summary)
        manifest = build_run_manifest(
            summary=summary,
            run_state=run_state,
            room_context=self.room_context,
            judge_enabled=self.judge_enabled,
            writeback_enabled=self.writeback_enabled,
        )
        if (self.out_dir / "mas_trace.jsonl").exists():
            manifest["artifacts"]["mas_trace"] = "mas_trace.jsonl"
        if (self.out_dir / "agentscope_events.jsonl").exists():
            manifest["artifacts"]["agentscope_events"] = "agentscope_events.jsonl"
        if (self.out_dir / "grounding_trace.jsonl").exists():
            manifest["artifacts"]["grounding_trace"] = "grounding_trace.jsonl"
        write_json(self.out_dir / "run_manifest.json", manifest)
        (self.out_dir / "run_summary.md").write_text(
            build_run_summary_md(
                summary=summary,
                output_files=output_files,
                writeback_status=self.writeback_status,
            ),
            encoding="utf-8",
        )
        write_json(self.out_dir / "run_state.json", run_state)

    def maybe_writeback(self, predictions: list[FieldPrediction], overlays: list[AgentOverlay]) -> None:
        if not self.writeback_enabled:
            self.writeback_status = "skipped: writeback disabled"
            return
        if self.template_path is None:
            self.writeback_status = "skipped: template path was not provided"
            return
        if not self.template_path.exists():
            self.writeback_status = "skipped: template file does not exist"
            return

        agent_review_items = list(self.review_items)
        overlay_by_field_id = {overlay.field_id: overlay for overlay in overlays}
        writeback_predictions = [
            prediction
            for prediction in predictions
            if prediction.answer_status == "answered" and overlay_by_field_id.get(prediction.field_id, default_blocking_overlay(prediction)).writeback_allowed
        ]
        summary = self.writeback_fn(
            template_path=self.template_path,
            predictions=writeback_predictions,
            output_path=self.out_dir / "filled_form.xlsx",
            trace_by_field={prediction.field_id: f"{self.run_id}:{prediction.field_id}" for prediction in writeback_predictions},
        )
        writeback_review_items = read_jsonl(self.out_dir / "review_items.jsonl")
        self.review_items = merge_review_items(agent_review_items, writeback_review_items)
        write_jsonl(self.out_dir / "review_items.jsonl", self.review_items)
        self.writeback_status = "completed"
        self.writeback_summary = summary.to_dict() if hasattr(summary, "to_dict") else dict(summary or {})

    def predictions_checkpoint_path(self) -> Path:
        return self.out_dir / "predictions.checkpoint.jsonl"

    def trace_checkpoint_path(self) -> Path:
        return self.out_dir / "trace.checkpoint.jsonl"

    def review_checkpoint_path(self) -> Path:
        return self.out_dir / "review_items.checkpoint.jsonl"

    def overlay_checkpoint_path(self) -> Path:
        return self.out_dir / "agent_overlays.checkpoint.jsonl"

    def eval_checkpoint_path(self) -> Path:
        return self.out_dir / "eval_results.checkpoint.jsonl"

    def run_metadata(self) -> dict[str, Any]:
        return {
            "engine": "step15_agent",
            "target_namespace": self.target_namespace,
            "global_namespace": self.global_namespace,
            "room_context": display_text(self.room_context),
            "retrieval_plan": self.retrieval_plan,
            "retrieval_fusion_mode": "dense",
            "grounding_enabled": self.grounding_enabled,
            "vector_top_k": self.vector_top_k,
            "rerank_top_n": self.rerank_top_n,
            "collection_name": self.collection_name,
            "qdrant_path": str(self.qdrant_path),
            "embedding_model": self.embedding_model,
            "rerank_model": self.rerank_model,
            "chat_model": self.chat_model,
            "chat_max_retries": self.chat_max_retries,
            "chat_retry_backoff_seconds": self.chat_retry_backoff_seconds,
            "prompt_version": self.prompt_version,
            "use_judge_cache": self.use_judge_cache,
            "judge_cache_path": str(self.judge_cache_path) if self.judge_cache_path else "",
            "judge_enabled": self.judge_enabled,
            "writeback_enabled": self.writeback_enabled,
        }


def convert_step15_generated_to_prediction(
    item: dict[str, Any],
    generated: dict[str, Any],
    top_hits: list[dict[str, Any]],
    *,
    method_name: str = "step15_agent",
    retrieval_mode: str = "layered",
) -> FieldPrediction:
    status = str(generated.get("answer_status") or "not_found")
    if status not in ANSWER_STATUSES:
        status = "conflict_unresolved"
    source_chunk_ids = [str(chunk_id) for chunk_id in generated.get("source_chunk_ids") or [] if chunk_id]
    evidence_attachment_ids = [str(item_id) for item_id in generated.get("evidence_attachment_ids") or [] if item_id]
    reference_source_documents = normalize_reference_source_documents(generated, top_hits)
    reference_chunk_ids = reference_chunk_ids_from_generated(generated, reference_source_documents)
    source_ids_valid = all(chunk_id in hit_index(top_hits) for chunk_id in source_chunk_ids)
    confidence = clamp_confidence(generated.get("confidence"))
    validation = {
        "engine": "step15_agent",
        "retrieval_mode": retrieval_mode,
        "agent_resolution": generated.get("agent_resolution"),
        "missing_fields": generated.get("missing_fields") or [],
        "notes": generated.get("notes"),
        "top_hit_count": len(top_hits),
        "source_ids_valid": source_ids_valid,
        "step15_generated": generated,
    }
    return FieldPrediction(
        field_id=field_id_for_item(item),
        row_index=int(item.get("row_index") or 0),
        target_cell=item.get("target_cell"),
        answer_value=generated.get("answer_value") or "未找到",
        answer_status=status,
        confidence=confidence,
        source_chunk_ids=source_chunk_ids,
        evidence_attachment_ids=evidence_attachment_ids,
        reference_chunk_ids=reference_chunk_ids,
        reference_source_documents=reference_source_documents,
        reference_snippets=reference_snippets(reference_source_documents, top_hits),
        validation=validation,
        method_name=method_name,
    )


def build_agent_overlay_for_step15_prediction(
    raw_prediction: FieldPrediction,
    top_hits: list[dict[str, Any]],
    critic_flags: list[str],
    *,
    min_reference_hits: int = 1,
) -> AgentOverlay:
    reference_docs = list(raw_prediction.reference_source_documents)
    reasons: list[str] = list(critic_flags)
    suggested_status: str | None = None
    suggested_answer_value: str | None = None
    suggested_reference_docs: list[dict[str, Any]] = []
    should_rescue_not_found = raw_prediction.answer_status == "not_found" and has_relevant_reference_hits(
        top_hits, min_reference_hits=min_reference_hits
    )
    should_fill_partial_refs = raw_prediction.answer_status == "partial_clue" and not reference_docs and len(top_hits) >= min_reference_hits
    downgrade_flags = [flag for flag in critic_flags if flag in RISKY_ANSWERED_DOWNGRADE_FLAGS]
    should_flag_risky_answered = raw_prediction.answer_status == "answered" and bool(downgrade_flags)

    if should_rescue_not_found:
        suggested_status = "partial_clue"
        suggested_answer_value = "未找到可直接填写的证据；检索到相关线索，请人工复核。"
        reasons.append("not_found_with_relevant_hits")
        suggested_reference_docs = reference_source_documents_from_hits(top_hits)
    elif should_fill_partial_refs:
        reasons.append("reference_docs_filled_by_runner")
        suggested_reference_docs = reference_source_documents_from_hits(top_hits)
    elif should_flag_risky_answered:
        suggested_status = "partial_clue"
        suggested_answer_value = "检索到相关线索，但证据不足以安全直接填写；请人工复核。"
        reasons.append("risky_answered_requires_review")
        suggested_reference_docs = reference_source_documents_from_hits(top_hits)

    if not suggested_reference_docs and reference_docs:
        suggested_reference_docs = reference_docs
    suggested_reference_ids = dedupe([str(doc.get("chunk_id")) for doc in suggested_reference_docs if doc.get("chunk_id")])
    suggested_snippets = reference_snippets(suggested_reference_docs, top_hits)
    critical_flags = [flag for flag in critic_flags if flag in CRITICAL_OVERLAY_FLAGS]
    if raw_prediction.answer_status == "answered" and not critical_flags:
        writeback_allowed = True
        review_required = False
    else:
        writeback_allowed = False
        review_required = True
    if raw_prediction.answer_status in {"partial_clue", "not_found", "conflict_unresolved"}:
        review_required = True
        writeback_allowed = False
    if critical_flags:
        review_required = True
        writeback_allowed = False
    if critical_flags:
        risk_level = "high"
    elif review_required or critic_flags:
        risk_level = "medium"
    else:
        risk_level = "low"
    return AgentOverlay(
        field_id=raw_prediction.field_id,
        row_index=raw_prediction.row_index,
        target_cell=raw_prediction.target_cell,
        critic_flags=critic_flags,
        review_required=review_required,
        writeback_allowed=writeback_allowed,
        suggested_status=suggested_status,
        suggested_answer_value=suggested_answer_value,
        suggested_reference_source_documents=suggested_reference_docs,
        suggested_reference_chunk_ids=suggested_reference_ids,
        suggested_reference_snippets=suggested_snippets,
        risk_level=risk_level,
        reasons=dedupe(reasons),
    )


def critic_check_step15_answer(
    item: dict[str, Any],
    generated: dict[str, Any],
    top_hits: list[dict[str, Any]],
) -> list[str]:
    flags: list[str] = []
    status = str(generated.get("answer_status") or "")
    source_chunk_ids = [str(chunk_id) for chunk_id in generated.get("source_chunk_ids") or [] if chunk_id]
    top_hit_index = hit_index(top_hits)
    source_hits = [top_hit_index[chunk_id] for chunk_id in source_chunk_ids if chunk_id in top_hit_index]
    question_text = display_text(item.get("question_text"))
    if status == "answered" and not source_chunk_ids:
        flags.append("answered_without_source")
    if any(chunk_id not in top_hit_index for chunk_id in source_chunk_ids):
        flags.append("invalid_source_reference")
    if status == "partial_clue" and not (generated.get("reference_source_documents") or []):
        flags.append("partial_without_reference")
    if status == "not_found" and (top_hits or any(hit.get("retrieval_layer") == "target_main_fact" for hit in top_hits)):
        flags.append("not_found_with_relevant_hits")
    if status == "not_found" and len(top_hits) >= 5:
        flags.append("not_found_with_many_hits")
    if status == "not_found" and any(hit.get("retrieval_layer") == "target_main_fact" for hit in top_hits):
        flags.append("not_found_with_target_main_fact")
    if status == "conflict_unresolved":
        flags.append("conflict_needs_review")
    if clamp_confidence(generated.get("confidence")) < 0.5:
        flags.append("low_confidence")
    if len(display_text(generated.get("answer_value"))) > 200:
        flags.append("answer_too_long")
    if status == "answered" and is_equipment_capacity_field(question_text) and answered_from_global_intro(source_chunk_ids, top_hit_index):
        flags.append("answered_from_global_intro_risk")
    if status == "answered" and "液冷" in question_text and source_hits and not any(hit_has_liquid_cooling_terms(hit) for hit in source_hits):
        flags.append("liquid_cooling_scope_mismatch")
    if status == "answered" and field_intent_source_mismatch(question_text, source_hits):
        flags.append("field_intent_source_mismatch")
    return dedupe(flags)


def make_step15_review_item(
    item: dict[str, Any],
    prediction: FieldPrediction,
    overlay: AgentOverlay,
    top_hits: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not overlay.review_required:
        return None
    return {
        "field_id": prediction.field_id,
        "row_index": prediction.row_index,
        "target_cell": prediction.target_cell,
        "question_text": item.get("question_text"),
        "answer_status": prediction.answer_status,
        "answer_value": prediction.answer_value,
        "confidence": prediction.confidence,
        "source_chunk_ids": prediction.source_chunk_ids,
        "reference_source_documents": prediction.reference_source_documents,
        "critic_flags": overlay.critic_flags,
        "agent_overlay": overlay.to_dict(),
        "suggested_status": overlay.suggested_status,
        "suggested_answer_value": overlay.suggested_answer_value,
        "suggested_reference_source_documents": overlay.suggested_reference_source_documents,
        "writeback_allowed": overlay.writeback_allowed,
        "risk_level": overlay.risk_level,
        "reasons": overlay.reasons,
        "top_hit_preview": top_hit_preview(top_hits),
        "suggested_action": suggested_review_action(prediction.answer_status, overlay.critic_flags, overlay),
    }


def parse_rows_arg(rows_text: str | None, *, step12_dir: Path) -> list[int]:
    text = (rows_text or "").strip()
    if not text or text.lower() == "all":
        return all_base_cloud_rows(step12_dir)
    rows: list[int] = []
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                raise ValueError(f"invalid row range: {token}")
            rows.extend(range(start, end + 1))
        else:
            rows.append(int(token))
    return rows


def validate_step15_agent_config(
    *,
    qdrant_path: Path | None,
    collection_name: str,
    embedding_endpoint: str,
    embedding_model: str,
    rerank_endpoint: str,
    chat_endpoint: str,
    chat_model: str,
) -> None:
    missing = [
        name
        for name, value in [
            ("qdrant_path", qdrant_path),
            ("collection_name", collection_name),
            ("embedding_endpoint", embedding_endpoint),
            ("embedding_model", embedding_model),
            ("rerank_endpoint", rerank_endpoint),
            ("chat_endpoint", chat_endpoint),
            ("chat_model", chat_model),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            "run-step15-agent requires qdrant_path, collection_name, embedding_endpoint, embedding_model, rerank_endpoint, chat_endpoint, chat_model"
        )


class TraceRecorderShim:
    def __init__(self, run_id: str, metadata: dict[str, Any] | None = None) -> None:
        from nested_doc_rag.agent.trace import TraceRecorder

        self._recorder = TraceRecorder(run_id, metadata=metadata)

    @property
    def events(self):  # noqa: ANN201
        return self._recorder.events

    def record(self, field_id: str | None, step: str, payload: dict[str, Any] | None = None) -> None:
        self._recorder.record(field_id, step, payload)

    def write_jsonl(self, path: Path) -> None:
        self._recorder.write_jsonl(path)

    def load_jsonl(self, path: Path) -> None:
        self._recorder.load_jsonl(path)

    def write_markdown(self, path: Path, summary: dict[str, Any]) -> None:
        lines = ["# Step15AgentRunner Trace", "", "## Summary", ""]
        for key in [
            "total_fields",
            "answered_count",
            "partial_clue_count",
            "not_found_count",
            "conflict_unresolved_count",
            "review_count",
            "failed_count",
            "skipped_completed_count",
        ]:
            lines.append(f"- {key}: {summary.get(key, 0)}")
        lines.extend(["", "## Events", ""])
        for event in self.events:
            lines.append(f"- `{event.step}` field=`{event.field_id}`")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def with_critic_validation(prediction: FieldPrediction, critic_flags: list[str]) -> FieldPrediction:
    validation = dict(prediction.validation)
    validation["critic_flags"] = critic_flags
    validation["needs_human_review"] = bool(critic_flags) or prediction.answer_status != "answered" or prediction.confidence < 0.5
    validation["validation_pass"] = not bool(UNSAFE_WRITEBACK_FLAGS.intersection(critic_flags))
    return replace(prediction, validation=validation)


def normalize_reference_source_documents(generated: dict[str, Any], top_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = hit_index(top_hits)
    output: list[dict[str, Any]] = []
    for item in generated.get("reference_source_documents") or []:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id") or "")
        hit = hits.get(chunk_id, {})
        output.append(
            {
                "chunk_id": chunk_id,
                "namespace": item.get("namespace") or hit.get("namespace"),
                "source_type": item.get("source_type") or hit.get("source_type"),
                "corpus_layer": item.get("corpus_layer") or hit.get("corpus_layer"),
                "retrieval_layer": item.get("retrieval_layer") or hit.get("retrieval_layer"),
                "source_anchor": item.get("source_anchor") or item.get("anchor") or hit.get("anchor"),
                "file_name": item.get("file_name") or hit.get("file_name"),
                "anchor": item.get("anchor") or hit.get("anchor"),
                "reason": item.get("reason") or "",
                "text_preview": item.get("text_preview") or display_text(hit.get("raw_text") or hit.get("text_for_embedding"), 180),
            }
        )
    return output


def reference_source_documents_from_hits(top_hits: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for hit in top_hits[:limit]:
        chunk_id = str(hit.get("chunk_id") or "")
        if not chunk_id:
            continue
        docs.append(
            {
                "chunk_id": chunk_id,
                "namespace": hit.get("namespace"),
                "source_type": hit.get("source_type"),
                "corpus_layer": hit.get("corpus_layer"),
                "retrieval_layer": hit.get("retrieval_layer"),
                "source_anchor": hit.get("source_anchor") or hit.get("anchor"),
                "file_name": hit.get("file_name"),
                "anchor": hit.get("anchor") or hit.get("source_anchor"),
                "reason": "retrieved related evidence, but not safe enough for direct filling",
                "text_preview": display_text(hit.get("raw_text") or hit.get("text_for_embedding"), 180),
            }
        )
    return docs


def has_relevant_reference_hits(top_hits: list[dict[str, Any]], *, min_reference_hits: int = 1) -> bool:
    if len(top_hits) < min_reference_hits:
        return False
    if top_hits:
        return True
    return any(
        hit.get("retrieval_layer") in {"target_main_fact", "target_structured_detail"}
        or (display_text(hit.get("namespace")) and hit.get("namespace") != "global")
        or safe_float(hit.get("rerank_score")) >= 0.5
        or safe_float(hit.get("vector_score")) >= 0.7
        for hit in top_hits
    )


def partial_confidence(confidence: float) -> float:
    return max(0.35, min(confidence or 0.45, 0.55))


def reference_chunk_ids_from_generated(generated: dict[str, Any], docs: list[dict[str, Any]]) -> list[str]:
    ids = [str(item) for item in generated.get("reference_chunk_ids") or [] if item]
    ids.extend(str(doc.get("chunk_id")) for doc in docs if doc.get("chunk_id"))
    return dedupe(ids)


def reference_snippets(docs: list[dict[str, Any]], top_hits: list[dict[str, Any]]) -> list[str]:
    hits = hit_index(top_hits)
    snippets: list[str] = []
    for doc in docs:
        text = doc.get("text_preview")
        if not text and doc.get("chunk_id") in hits:
            hit = hits[str(doc["chunk_id"])]
            text = hit.get("raw_text") or hit.get("text_for_embedding")
        if text:
            snippets.append(display_text(text, 160))
    return snippets


def hit_index(top_hits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(hit.get("chunk_id")): hit for hit in top_hits if hit.get("chunk_id")}


def answered_from_global_intro(source_chunk_ids: list[str], hits: dict[str, dict[str, Any]]) -> bool:
    source_hits = [hits[chunk_id] for chunk_id in source_chunk_ids if chunk_id in hits]
    if not source_hits:
        return False
    global_intro_count = sum(
        1
        for hit in source_hits
        if hit.get("retrieval_layer") == "global_intro"
        or (hit.get("namespace") == "global" and str(hit.get("source_type") or "").startswith("intro_doc"))
    )
    return global_intro_count >= max(1, len(source_hits) // 2)


def hit_has_liquid_cooling_terms(hit: dict[str, Any]) -> bool:
    text = display_text(" ".join([display_text(hit.get("raw_text")), display_text(hit.get("text_for_embedding"))]))
    return any(term in text for term in ["液冷", "CDU", "冷板", "液冷机柜"])


def is_equipment_capacity_field(question_text: str) -> bool:
    return any(
        term in question_text
        for term in [
            "UPS",
            "电池",
            "市电",
            "供电",
            "油机",
            "柴油",
            "发电",
            "机柜",
            "U位",
            "功率",
            "容量",
            "空调",
            "制冷",
            "网络",
            "端口",
            "液冷",
            "冷板",
            "CDU",
        ]
    )


def field_intent_source_mismatch(question_text: str, source_hits: list[dict[str, Any]]) -> bool:
    if not source_hits:
        return False
    asks_record = any(term in question_text for term in ["巡检", "记录", "归档", "报告", "演练", "测试", "维护", "检修"])
    if not asks_record:
        return False
    source_text = display_text(" ".join(display_text(hit.get("raw_text") or hit.get("text_for_embedding")) for hit in source_hits))
    has_record_terms = any(term in source_text for term in ["巡检", "记录", "归档", "报告", "演练", "测试", "维护", "检修"])
    has_equipment_terms = any(term in source_text for term in ["UPS", "机柜", "功率", "容量", "市电", "油机", "空调", "冷冻", "供电"])
    return has_equipment_terms and not has_record_terms


def is_retryable_chat_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ["timeout", "timed out", "curl: (28)", "operation timed out", "read timed out"])


def is_json_parse_error(exc: Exception) -> bool:
    return isinstance(exc, (json.JSONDecodeError, JsonRepairError))


def make_eval_result(
    item: dict[str, Any],
    generated: dict[str, Any],
    judge: dict[str, Any],
    top_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    masked_query: str,
    room_context: str | None,
) -> dict[str, Any]:
    return {
        "row_index": item.get("row_index"),
        "target_cell": item.get("target_cell"),
        "category_path": item.get("category_path") or [],
        "question_text": item.get("question_text"),
        "instruction_text": item.get("instruction_text"),
        "answer_example_format_only": item.get("answer_example"),
        "external_room_context": display_text(room_context),
        "heldout_answer": item.get("existing_value") or item.get("heldout_answer") or "",
        "masked_query": masked_query,
        "generated_answer": generated,
        "judge": judge,
        "top_hits": top_hits,
        "vector_hits": vector_hits[:10],
    }


def build_trace_summary(
    events: list[Any],
    predictions: list[FieldPrediction],
    review_items: list[dict[str, Any]],
    overlays: list[AgentOverlay] | None = None,
) -> dict[str, Any]:
    status_counts = Counter(prediction.answer_status for prediction in predictions)
    overlays = overlays or []
    critic_flags = Counter(flag for overlay in overlays for flag in overlay.critic_flags)
    retrieval_latencies = [
        float(event.payload.get("retrieval_latency_ms") or 0)
        for event in events
        if event.step == "layered_retrieval_finished" and event.payload.get("retrieval_latency_ms") is not None
    ]
    generation_latencies = [
        float(event.payload.get("generation_latency_ms") or 0)
        for event in events
        if event.step == "answer_arbitrated" and event.payload.get("generation_latency_ms") is not None
    ]
    evidence_strengths = Counter(
        str(event.payload.get("evidence_strength"))
        for event in events
        if event.step == "grounding_evaluated" and event.payload.get("evidence_strength")
    )
    field_bindings = Counter(
        str(event.payload.get("field_binding"))
        for event in events
        if event.step == "grounding_evaluated" and event.payload.get("field_binding")
    )
    return {
        "total_fields": len(predictions),
        "answered_count": status_counts.get("answered", 0),
        "partial_clue_count": status_counts.get("partial_clue", 0),
        "not_found_count": status_counts.get("not_found", 0),
        "conflict_unresolved_count": status_counts.get("conflict_unresolved", 0),
        "review_count": len(review_items),
        "critic_flag_counts": dict(critic_flags),
        "raw_status_counts": dict(status_counts),
        "overlay_counts": build_overlay_counts(overlays),
        "avg_retrieval_latency_ms": round(sum(retrieval_latencies) / len(retrieval_latencies), 3) if retrieval_latencies else 0,
        "avg_generation_latency_ms": round(sum(generation_latencies) / len(generation_latencies), 3) if generation_latencies else 0,
        "failed_count": sum(1 for event in events if event.step == "field_failed"),
        "resumed_count": sum(1 for event in events if event.step == "resume_started"),
        "skipped_completed_count": sum(int(event.payload.get("skipped_completed_count") or 0) for event in events if event.step == "resume_started"),
        "evidence_strength_distribution": dict(evidence_strengths),
        "field_binding_distribution": dict(field_bindings),
    }


def build_summary_json(
    *,
    predictions: list[FieldPrediction],
    overlays: list[AgentOverlay],
    eval_results: list[dict[str, Any]],
    trace_summary: dict[str, Any],
    run_state: dict[str, Any],
    writeback_status: str,
    writeback_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    label_counts = Counter(result.get("judge", {}).get("label") for result in eval_results)
    numeric_scores = [float(result.get("judge", {}).get("score") or 0) for result in eval_results]
    return {
        **run_state,
        "method_name": "step15_agent",
        "effect_metrics_source": "predictions_raw.jsonl",
        "production_controls_source": "agent_overlays.jsonl",
        "answer_status_counts": dict(Counter(prediction.answer_status for prediction in predictions)),
        "raw_status_counts": dict(Counter(prediction.answer_status for prediction in predictions)),
        "overlay_counts": build_overlay_counts(overlays),
        "trace_summary": trace_summary,
        "field_binding_distribution": trace_summary.get("field_binding_distribution", {}),
        "label_counts": dict(label_counts),
        "average_score": round(sum(numeric_scores) / len(numeric_scores), 4) if numeric_scores else 0,
        "acceptable_or_better": sum(1 for result in eval_results if result.get("judge", {}).get("label") in {"exact", "acceptable"}),
        "partial_or_better": sum(1 for result in eval_results if result.get("judge", {}).get("label") in {"exact", "acceptable", "partial"}),
        "writeback_status": writeback_status,
        "writeback_summary": writeback_summary or {},
    }


def build_run_manifest(
    *,
    summary: dict[str, Any],
    run_state: dict[str, Any],
    room_context: str | None,
    judge_enabled: bool,
    writeback_enabled: bool,
) -> dict[str, Any]:
    trace_summary = summary.get("trace_summary") or {}
    raw_status_counts = summary.get("raw_status_counts") or {}
    overlay_counts = summary.get("overlay_counts") or {}
    failed_count = int(trace_summary.get("failed_count") or run_state.get("fields_failed") or 0)
    total_fields = int(summary.get("fields_total") or trace_summary.get("total_fields") or 0)
    if total_fields and failed_count >= total_fields:
        status = "failed"
    elif failed_count:
        status = "completed_with_failures"
    else:
        status = "completed"
    artifacts = {
        "predictions_raw": "predictions_raw.jsonl",
        "predictions": "predictions.jsonl",
        "agent_overlays": "agent_overlays.jsonl",
        "predictions_agent_view": "predictions_agent_view.jsonl",
        "review_items": "review_items.jsonl",
        "trace": "trace.jsonl",
        "trace_summary": "trace_summary.json",
        "run_summary": "run_summary.md",
        "summary": "summary.json",
        "filled_form": "filled_form.xlsx" if writeback_enabled else None,
        "writeback_audit": "writeback_audit.jsonl" if writeback_enabled else None,
        "evidence_map": "evidence_map.json" if writeback_enabled else None,
    }
    return {
        "run_id": summary.get("run_id"),
        "created_at": run_state.get("started_at"),
        "finished_at": run_state.get("finished_at"),
        "status": status,
        "engine": "step15_agent_overlay",
        "target_namespace": summary.get("target_namespace"),
        "global_namespace": summary.get("global_namespace"),
        "room_context": display_text(room_context),
        "rows": run_state.get("rows", ""),
        "retrieval_plan": summary.get("retrieval_plan", "layered"),
        "judge_enabled": judge_enabled,
        "writeback_enabled": writeback_enabled,
        "artifacts": artifacts,
        "counts": {
            "total_fields": total_fields,
            "answered": int(raw_status_counts.get("answered") or 0),
            "partial_clue": int(raw_status_counts.get("partial_clue") or 0),
            "not_found": int(raw_status_counts.get("not_found") or 0),
            "conflict_unresolved": int(raw_status_counts.get("conflict_unresolved") or 0),
            "review_required": int(overlay_counts.get("review_required") or 0),
            "writeback_allowed": int(overlay_counts.get("writeback_allowed") or 0),
            "failed": failed_count,
        },
    }


def build_overlay_counts(overlays: list[AgentOverlay]) -> dict[str, Any]:
    critic_flags = Counter(flag for overlay in overlays for flag in overlay.critic_flags)
    return {
        "review_required": sum(1 for overlay in overlays if overlay.review_required),
        "writeback_allowed": sum(1 for overlay in overlays if overlay.writeback_allowed),
        "suggested_partial_clue": sum(1 for overlay in overlays if overlay.suggested_status == "partial_clue"),
        "risk_levels": dict(Counter(overlay.risk_level for overlay in overlays)),
        "critic_flag_counts": dict(critic_flags),
    }


def build_run_summary_md(*, summary: dict[str, Any], output_files: list[str], writeback_status: str) -> str:
    trace_summary = summary.get("trace_summary") or {}
    overlay_counts = summary.get("overlay_counts") or {}
    lines = [
        "# Step15AgentRunner Run Summary",
        "",
        f"- run_id: `{summary.get('run_id')}`",
        f"- target_namespace: `{summary.get('target_namespace')}`",
        f"- global_namespace: `{summary.get('global_namespace')}`",
        f"- retrieval_plan: `{summary.get('retrieval_plan')}`",
        f"- total_fields: {summary.get('fields_total')}",
        f"- answered: {trace_summary.get('answered_count', 0)}",
        f"- partial_clue: {trace_summary.get('partial_clue_count', 0)}",
        f"- not_found: {trace_summary.get('not_found_count', 0)}",
        f"- conflict_unresolved: {trace_summary.get('conflict_unresolved_count', 0)}",
        f"- review_count: {trace_summary.get('review_count', 0)}",
        f"- failed_count: {trace_summary.get('failed_count', 0)}",
        f"- skipped_completed_count: {trace_summary.get('skipped_completed_count', 0)}",
        f"- average_score: {summary.get('average_score', 0)}",
        f"- exact_or_acceptable: {summary.get('acceptable_or_better', 0)}",
        f"- partial_or_better: {summary.get('partial_or_better', 0)}",
        f"- overlay_review_required: {overlay_counts.get('review_required', 0)}",
        f"- overlay_writeback_allowed: {overlay_counts.get('writeback_allowed', 0)}",
        f"- overlay_suggested_partial_clue: {overlay_counts.get('suggested_partial_clue', 0)}",
        f"- writeback: {writeback_status}",
        "",
        "## Runtime Model",
        "",
        "Step 15 layered RAG is the effect engine. The Agent layer manages field execution, trace, checkpoint/resume, critic flags, review routing, and optional safe writeback.",
        "",
        "## Output Files",
        "",
    ]
    lines.extend(f"- `{file_name}`" for file_name in output_files)
    return "\n".join(lines).rstrip() + "\n"


def minimal_item_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "form_item_id": item.get("form_item_id"),
        "file_name": item.get("file_name"),
        "sheet_name": item.get("sheet_name"),
        "row_index": item.get("row_index"),
        "target_cell": item.get("target_cell"),
        "category_path": item.get("category_path") or [],
        "question_text": item.get("question_text"),
        "instruction_text": item.get("instruction_text"),
        "answer_example_format_only": item.get("answer_example"),
        "needs_evidence": item.get("needs_evidence"),
    }


def field_id_for_item(item: dict[str, Any]) -> str:
    return str(item.get("form_item_id") or f"row_{item.get('row_index')}")


def item_key(item: dict[str, Any]) -> str:
    if item.get("form_item_id"):
        return f"field:{item['form_item_id']}"
    return f"row:{int(item.get('row_index') or 0)}"


def completed_item_keys(predictions: Any) -> set[str]:
    keys: set[str] = set()
    for prediction in predictions:
        keys.add(f"field:{prediction.field_id}")
        keys.add(f"row:{prediction.row_index}")
    return keys


def ordered_predictions_for_items(items: list[dict[str, Any]], predictions_by_field_id: dict[str, FieldPrediction]) -> list[FieldPrediction]:
    by_key: dict[str, FieldPrediction] = {}
    for prediction in predictions_by_field_id.values():
        by_key[f"field:{prediction.field_id}"] = prediction
        by_key[f"row:{prediction.row_index}"] = prediction
    ordered: list[FieldPrediction] = []
    for item in items:
        prediction = by_key.get(item_key(item)) or by_key.get(f"field:{field_id_for_item(item)}")
        if prediction is not None and prediction not in ordered:
            ordered.append(prediction)
    return sorted(ordered, key=lambda prediction: (prediction.row_index, prediction.field_id))


def rows_label_for_items(items: list[dict[str, Any]]) -> str:
    rows = sorted(int(item.get("row_index") or 0) for item in items if item.get("row_index") is not None)
    if not rows:
        return ""
    if rows == list(range(rows[0], rows[-1] + 1)):
        return f"{rows[0]}-{rows[-1]}"
    return ",".join(str(row) for row in rows)


def ordered_overlays_for_predictions(predictions: list[FieldPrediction], overlays_by_field_id: dict[str, AgentOverlay]) -> list[AgentOverlay]:
    return [overlays_by_field_id.get(prediction.field_id) or default_blocking_overlay(prediction) for prediction in predictions]


def build_agent_view_records(predictions: list[FieldPrediction], overlays: list[AgentOverlay]) -> list[dict[str, Any]]:
    overlay_by_field_id = {overlay.field_id: overlay for overlay in overlays}
    records: list[dict[str, Any]] = []
    for prediction in predictions:
        overlay = overlay_by_field_id.get(prediction.field_id) or default_blocking_overlay(prediction)
        records.append({**prediction.to_dict(), "agent_overlay": overlay.to_dict()})
    return records


def default_blocking_overlay(prediction: FieldPrediction) -> AgentOverlay:
    return AgentOverlay(
        field_id=prediction.field_id,
        row_index=prediction.row_index,
        target_cell=prediction.target_cell,
        critic_flags=[],
        review_required=True,
        writeback_allowed=False,
        suggested_status=None,
        suggested_answer_value=None,
        suggested_reference_source_documents=[],
        suggested_reference_chunk_ids=[],
        suggested_reference_snippets=[],
        risk_level="medium",
        reasons=["missing_overlay"],
    )


def load_judge_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    cache: dict[str, dict[str, Any]] = {}
    for record in read_jsonl(path):
        cache_key = str(record.get("cache_key") or "")
        judge = record.get("judge")
        if cache_key and isinstance(judge, dict):
            cache[cache_key] = dict(judge)
    return cache


def append_judge_cache_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_judge_cache_key(
    *,
    item: dict[str, Any],
    generated: dict[str, Any],
    heldout_answer: str,
    judge_prompt_version: str,
    judge_model: str,
) -> str:
    payload = {
        "field_id": field_id_for_item(item),
        "question_text_hash": stable_hash(item.get("question_text")),
        "heldout_answer_hash": stable_hash(heldout_answer),
        "raw_answer_value_hash": stable_hash(generated.get("answer_value")),
        "raw_answer_status": generated.get("answer_status"),
        "source_chunk_ids_hash": stable_hash(generated.get("source_chunk_ids") or []),
        "reference_source_documents_hash": stable_hash(generated.get("reference_source_documents") or []),
        "judge_prompt_version": judge_prompt_version,
        "judge_model": judge_model,
    }
    return stable_hash(payload)


def stable_hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ordered_eval_results_for_items(items: list[dict[str, Any]], eval_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_row = {int(result.get("row_index") or 0): result for result in eval_results}
    return [by_row[int(item.get("row_index") or 0)] for item in items if int(item.get("row_index") or 0) in by_row]


def ordered_eval_results_for_items_by_predictions(predictions: list[FieldPrediction], eval_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_row = {int(result.get("row_index") or 0): result for result in eval_results}
    return [by_row[prediction.row_index] for prediction in predictions if prediction.row_index in by_row]


def count_layers(hits: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(hit.get("retrieval_layer") or "unknown") for hit in hits))


def chunk_ids(hits: list[dict[str, Any]]) -> list[str]:
    return [str(hit.get("chunk_id")) for hit in hits if hit.get("chunk_id")]


def top_hit_preview(top_hits: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": hit.get("chunk_id"),
            "retrieval_layer": hit.get("retrieval_layer"),
            "namespace": hit.get("namespace"),
            "source_type": hit.get("source_type"),
            "file_name": hit.get("file_name"),
            "anchor": hit.get("anchor"),
            "text_preview": display_text(hit.get("raw_text") or hit.get("text_for_embedding"), 120),
        }
        for hit in top_hits[:limit]
    ]


def suggested_review_action(answer_status: str, critic_flags: list[str], overlay: AgentOverlay | None = None) -> str:
    if overlay and not overlay.writeback_allowed and answer_status == "answered":
        return "核对 source_chunk_ids 与答案一致性；overlay 已阻止自动回写。"
    if overlay and overlay.suggested_status == "partial_clue":
        return "根据 overlay 建议和参考来源人工确认是否可填写。"
    if answer_status == "partial_clue":
        return "根据 reference_source_documents 人工确认是否可填写。"
    if answer_status == "not_found":
        return "检查检索结果或补充知识库。"
    if answer_status == "conflict_unresolved":
        return "人工裁决冲突证据。"
    if critic_flags:
        return "核对 source_chunk_ids 与答案一致性。"
    return "人工复核参考来源后确认是否填写"


def merge_review_items(agent_items: list[dict[str, Any]], writeback_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source, items in [("agent", agent_items), ("writeback", writeback_items)]:
        for item in items:
            key = (str(item.get("field_id") or ""), str(item.get("reason") or item.get("answer_status") or ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append({"source": source, **item})
    return merged


def clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(number, 1.0))


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def perf_counter_ms() -> float:
    return perf_counter() * 1000


def now_iso() -> str:
    from nested_doc_rag.agent.trace import now_iso as trace_now_iso

    return trace_now_iso()
