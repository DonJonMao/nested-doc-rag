from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Literal
from uuid import uuid4

from nested_doc_rag import model_gateway
from nested_doc_rag.config import AppConfig
from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.evaluation.step15_engine import (
    Step15RetrievalResult,
    add_room_context,
    build_qdrant_answer_messages,
    run_step15_retrieval,
)
from nested_doc_rag.evidence_record import EvidenceAddress
from nested_doc_rag.evidence_resolver import resolve_evidence_refs, validate_evidence_ref
from nested_doc_rag.excel.writeback import patch_workbook
from nested_doc_rag.form.input_snapshot import (
    EVIDENCE_CONTRACT_VERSION,
    build_acquisition_contract,
    build_form_input_snapshot,
    persist_form_input_snapshot,
)
from nested_doc_rag.gongkan_eval import build_judge_messages, build_masked_query, call_deepseek_json, select_form_items
from nested_doc_rag.grounding import EvidenceStrengthEvaluator, EvidenceStrengthResult, apply_evidence_strength_to_overlay
from nested_doc_rag.grounding.evidence_strength import max_risk_level
from nested_doc_rag.grounding.provenance import build_field_evidence, finalize_evidence, source_reference
from nested_doc_rag.grounding.sufficiency import (
    EvidenceSufficiency,
    build_sufficiency_messages,
    build_targeted_query,
    normalize_sufficiency,
)
from nested_doc_rag.io import display_text, read_jsonl, write_json, write_jsonl
from nested_doc_rag.llm import JsonRepairError
from nested_doc_rag.retrieval import QdrantRetriever, attach_parent_payloads
from nested_doc_rag.retrieval.layered import constrain_layered_plan, filter_hits_by_plan
from nested_doc_rag.schemas.eval import FieldPrediction

from .binding import (
    FieldBindingAgentResult,
    build_field_binding_messages,
    normalize_field_binding_agent_result,
    select_binding_hits,
)
from .mas.controller import Step15MASController
from .slotting import (
    EMPTY_DECOMPOSITION,
    SlotConsistencyResult,
    SlotDecomposition,
    build_slot_decomposition_messages,
    evaluate_slot_consistency,
    heuristic_slot_decomposition,
    normalize_slot_decomposition,
    slot_cache_key,
)

AnswerCaller = Callable[..., dict[str, Any]]
JudgeCaller = Callable[..., dict[str, Any]]
SlotDecomposerCaller = Callable[..., dict[str, Any]]
FieldBindingJudgeCaller = Callable[..., dict[str, Any]]
RetrievalFn = Callable[[str], Step15RetrievalResult]
WritebackFn = Callable[..., Any]

ANSWER_STATUSES = {"answered", "partial_clue", "not_found", "conflict_unresolved"}
PROMPT_VERSIONS = {"step15_compat", "agent_v2", "agentic_v1"}
UNSAFE_WRITEBACK_FLAGS = {
    "answered_without_source",
    "invalid_source_reference",
    "answered_from_global_intro_risk",
    "answer_too_long",
    "scope_mismatch_risk",
    "liquid_cooling_scope_mismatch",
    "field_intent_source_mismatch",
    "field_mismatch",
    "scope_mismatch",
    "status_mismatch",
    "slot_mismatch",
    "answer_evidence_mismatch",
}
RISKY_ANSWERED_DOWNGRADE_FLAGS = {
    "answered_without_source",
    "invalid_source_reference",
    "answered_from_global_intro_risk",
    "scope_mismatch_risk",
    "liquid_cooling_scope_mismatch",
    "field_intent_source_mismatch",
    "field_mismatch",
    "scope_mismatch",
    "status_mismatch",
    "slot_mismatch",
    "answer_evidence_mismatch",
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
        global_namespace: str,
        room_context: str | None = None,
        retrieval_plan: Literal["layered"] = "layered",
        vector_top_k: int | None = None,
        rerank_top_n: int | None = None,
        judge_enabled: bool = False,
        writeback_enabled: bool = False,
        overwrite_all_cli: bool = False,
        template_path: Path | None = None,
        checkpoint_every: int = 1,
        resume: bool = False,
        form_input_snapshot: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
        chat_max_retries: int = 2,
        chat_retry_backoff_seconds: int = 3,
        prompt_version: str = "step15_compat",
        judge_cache_path: Path | None = None,
        use_judge_cache: bool = False,
        deepseek_api_key_env: str | None = None,
        qdrant_path: Path | None = None,
        collection_name: str | None = None,
        index_scopes: list[dict[str, Any]] | None = None,
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
        field_binding_enabled: bool | None = None,
        parent_payload_enabled: bool | None = None,
        retriever: QdrantRetriever | None = None,
        reranker: RerankClient | None = None,
        retrieval_fn: RetrievalFn | None = None,
        answer_caller: AnswerCaller | None = None,
        judge_caller: JudgeCaller | None = None,
        slot_decomposer_caller: SlotDecomposerCaller | None = None,
        field_binding_judge_caller: FieldBindingJudgeCaller | None = None,
        sufficiency_caller: AnswerCaller | None = None,
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
        self.overwrite_all_cli = overwrite_all_cli
        if config.writeback.existing_value_policy == "overwrite_all" and not overwrite_all_cli:
            raise ValueError("overwrite_all requires an explicit CLI option")
        self.template_path = template_path
        self.checkpoint_every = max(1, checkpoint_every)
        self.resume = resume
        self.form_input_snapshot = form_input_snapshot
        self.timeout_seconds = timeout_seconds or config.services.timeout_seconds
        self.chat_max_retries = max(0, chat_max_retries)
        if model_gateway.is_enabled():
            self.chat_max_retries = min(self.chat_max_retries, 1)
        self.chat_retry_backoff_seconds = max(0, chat_retry_backoff_seconds)
        self.mas_mode = config.agentscope.mode if config.agentscope.enabled or config.agentscope.mode != "off" else "off"
        self.sufficiency_enabled = bool(config.retrieval.sufficiency_enabled)
        self.schema_first_enabled = bool(config.retrieval.schema_first_enabled)
        if self.schema_first_enabled and retrieval_fn is not None:
            raise ValueError("schema-first retrieval requires the schema and value retriever; a value-only retrieval_fn cannot run A4")
        if self.sufficiency_enabled and self.mas_mode != "off":
            raise ValueError("sufficiency-guided retrieval requires agentscope.mode=off; explicitly disable sufficiency for legacy MAS")
        if self.mas_mode not in {"off", "equivalent_mas", "trace_only", "agentic_mas"}:
            raise ValueError(f"unsupported agentscope.mode: {self.mas_mode}")
        self.agentic_mas_config = config.agentic_mas
        if prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"unsupported prompt_version: {prompt_version}")
        self.prompt_version = prompt_version
        self.agentic_prompt_version = config.agentic_mas.prompt_version if self.mas_mode == "agentic_mas" else self.prompt_version
        if self.agentic_prompt_version not in PROMPT_VERSIONS:
            raise ValueError(f"unsupported agentic_mas.prompt_version: {self.agentic_prompt_version}")
        self.judge_cache_path = judge_cache_path
        self.use_judge_cache = use_judge_cache
        self.judge_cache: dict[str, dict[str, Any]] = load_judge_cache(judge_cache_path) if use_judge_cache and judge_cache_path else {}
        self.deepseek_api_key_env = deepseek_api_key_env or config.services.chat_api_key_env
        self.qdrant_path = qdrant_path or config.paths.qdrant_path
        self.qdrant_url = config.qdrant.url
        self.qdrant_api_key_env = config.qdrant.api_key_env
        self.collection_name = collection_name or config.qdrant.collection_name
        from nested_doc_rag.retrieval.version_scope import normalize_index_scopes
        self.index_scopes = normalize_index_scopes(index_scopes, collection_name=self.collection_name,
                                                  namespaces=[target_namespace, global_namespace])
        if self.index_scopes is not None and {scope["namespace"] for scope in self.index_scopes} != {target_namespace, global_namespace}:
            raise ValueError("fill index scopes must contain exactly the target and global namespaces")
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
        self.field_binding_enabled = (
            bool(config.grounding.field_binding_enabled) if field_binding_enabled is None else bool(field_binding_enabled)
        )
        self.field_binding_agent_enabled = bool(config.grounding.field_binding_agent_enabled)
        self.slot_decomposition_enabled = bool(config.grounding.slot_decomposition_enabled)
        self.pre_writeback_consistency_enabled = bool(config.grounding.pre_writeback_consistency_enabled)
        self.relaxed_writeback_gate_enabled = bool(config.grounding.relaxed_writeback_gate_enabled)
        self.parent_payload_enabled = (
            bool(config.retrieval.expand_parent_payload) if parent_payload_enabled is None else bool(parent_payload_enabled)
        )
        self.answer_caller = answer_caller
        self.judge_caller = judge_caller
        self.slot_decomposer_caller = slot_decomposer_caller
        self.field_binding_judge_caller = field_binding_judge_caller
        self.sufficiency_caller = sufficiency_caller
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
                "agentic_prompt_version": self.agentic_prompt_version,
                "use_judge_cache": self.use_judge_cache,
                "judge_enabled": self.judge_enabled,
                "writeback_enabled": self.writeback_enabled,
                "field_binding_agent_enabled": self.field_binding_agent_enabled,
                "slot_decomposition_enabled": self.slot_decomposition_enabled,
                "pre_writeback_consistency_enabled": self.pre_writeback_consistency_enabled,
                "relaxed_writeback_gate_enabled": self.relaxed_writeback_gate_enabled,
            },
        )
        self.review_items: list[dict[str, Any]] = []
        self.eval_results: list[dict[str, Any]] = []
        self.retrieval_evidence_by_field_id: dict[str, list[dict[str, Any]]] = {}
        self.agent_overlays: list[AgentOverlay] = []
        self.evidence_provenance_by_field_id: dict[str, dict[str, Any]] = {}
        self.evidence_items_by_field_id: dict[str, dict[str, Any]] = {}
        self.grounding_trace_records: list[dict[str, Any]] = []
        self.slot_trace_records: list[dict[str, Any]] = []
        self.slot_decomposition_cache: dict[str, SlotDecomposition] = {}
        self.writeback_status = "skipped: writeback disabled"
        self.writeback_summary: dict[str, Any] | None = None
        self.mas_controller = (
            Step15MASController(self, mode=self.mas_mode, agentscope_enabled=config.agentscope.enabled)
            if self.mas_mode in {"equivalent_mas", "trace_only", "agentic_mas"}
            else None
        )
        self._owns_retriever = retriever is None and retrieval_fn is None
        self.retriever = retriever
        self.reranker = reranker
        if retrieval_fn is None:
            self.retriever = self.retriever or QdrantRetriever(
                qdrant_path=self.qdrant_path,
                qdrant_url=self.qdrant_url,
                qdrant_api_key_env=self.qdrant_api_key_env,
                collection_name=self.collection_name,
                index_scopes=self.index_scopes,
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
            if self.index_scopes is not None and normalize_index_scopes(
                getattr(self.retriever, "index_scopes", None), collection_name=self.collection_name,
                namespaces=[target_namespace, global_namespace],
            ) != self.index_scopes:
                raise ValueError("injected retriever does not match the frozen index scopes")
    def run(self, items: list[dict[str, Any]]) -> list[FieldPrediction]:
        items = select_form_items(items, None)
        snapshot = self.form_input_snapshot or build_form_input_snapshot(
            items,
            template_path=self.template_path,
            target_namespace=self.target_namespace,
            global_namespace=self.global_namespace,
            room_context=self.room_context,
            acquisition_contract=self.acquisition_contract(),
        )
        if snapshot["selected_field_ids"] != [str(item["form_item_id"]) for item in items]:
            raise RuntimeError("form input snapshot does not match the selected runtime fields")
        if snapshot.get("evidence_contract_version") != EVIDENCE_CONTRACT_VERSION:
            raise RuntimeError("cannot resume: evidence contract version changed; start a new run directory")
        if snapshot.get("acquisition_contract") != self.acquisition_contract():
            raise RuntimeError("cannot resume: retrieval strategy or prompt version changed; start a new run directory")
        persist_form_input_snapshot(self.out_dir, snapshot, resume=self.resume)
        self.form_input_snapshot = snapshot
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self.index_scopes is not None:
            write_json(self.out_dir / "index_scopes.json", self.index_scopes)
        self.evidence_items_by_field_id = {field_id_for_item(item): item for item in items}
        checkpoint_predictions = self.load_checkpoint_predictions() if self.resume else {}
        completed_keys = completed_item_keys(checkpoint_predictions.values())
        if self.resume:
            self.load_checkpoint_sidecars()
            self.validate_checkpoint_evidence(checkpoint_predictions, items)

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
            "acquisition_contract": self.acquisition_contract(),
            "fields_total": len(items),
            "fields_completed": skipped_completed_count,
            "fields_failed": 0,
            "resumed_count": 1 if self.resume and checkpoint_predictions else 0,
            "skipped_completed_count": skipped_completed_count,
            "judge_enabled": self.judge_enabled,
            "writeback_enabled": self.writeback_enabled,
            "field_binding_agent_enabled": self.field_binding_agent_enabled,
            "relaxed_writeback_gate_enabled": self.relaxed_writeback_gate_enabled,
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
                self.retrieval_evidence_by_field_id[result.prediction.field_id] = result.top_hits
                overlays_by_field_id[result.overlay.field_id] = result.overlay
                self.evidence_provenance_by_field_id[result.prediction.field_id] = build_field_evidence(
                    item=result.item,
                    prediction=result.prediction,
                    generated=result.generated,
                    top_hits=result.top_hits,
                    overlay=result.overlay,
                )
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
        if self.mas_mode == "agentic_mas" and self.mas_controller is not None:
            base_result = self._process_item_original(item)
            if base_result.overlay.writeback_allowed:
                self.mas_controller.record_live_base_preserved(item=item, base_result=base_result)
                result = base_result
            else:
                result = self.mas_controller.process_item_agentic_from_base(
                    item,
                    base_result=base_result,
                    config=self.agentic_mas_config,
                )
        elif self.mas_mode == "equivalent_mas" and self.mas_controller is not None:
            result = self._process_item_equivalent_mas(item)
        else:
            result = self._process_item_original(item)
            if self.mas_mode == "trace_only" and self.mas_controller is not None:
                self.mas_controller.record_trace_only_result(item, result)
        self.validate_pinned_hits(result.top_hits)
        self.validate_pinned_hits(result.vector_hits)
        prediction = attach_addressed_evidence(result.prediction, result.top_hits)
        overlay = apply_addressed_evidence_gate(prediction, result.overlay)
        if self.sufficiency_enabled:
            overlay = apply_sufficiency_gate(prediction, overlay, result.top_hits)
        result = replace(
            result, prediction=prediction, overlay=overlay,
            critic_flags=overlay.critic_flags,
            review_item=make_step15_review_item(item, prediction, overlay, result.top_hits),
        )
        self.trace.record(prediction.field_id, "evidence_refs_resolved", prediction.validation["addressable_evidence"])
        return result

    def validate_checkpoint_evidence(self, predictions: dict[str, FieldPrediction], items: list[dict[str, Any]]) -> None:
        """Recheck saved references before a completed field can be reused."""
        overlays = {overlay.field_id: overlay for overlay in self.agent_overlays}
        items_by_id = {field_id_for_item(item): item for item in items}
        for field_id, prediction in predictions.items():
            if field_id not in self.retrieval_evidence_by_field_id:
                raise RuntimeError(f"cannot resume: retrieval authority missing for {field_id}; start a new run directory")
            hits = self.retrieval_evidence_by_field_id[field_id]
            resolved = attach_addressed_evidence(prediction, hits)
            if [ref.to_dict() for ref in resolved.evidence_refs] != [ref.to_dict() for ref in prediction.evidence_refs]:
                raise RuntimeError(f"cannot resume: evidence references changed or failed verification for {field_id}")
            authority = hit_index(hits)
            for ref in prediction.evidence_refs:
                if validate_evidence_ref(ref, authority.get(ref.chunk_id)):
                    raise RuntimeError(f"cannot resume: invalid evidence reference for {field_id}")
            overlay = overlays.get(field_id)
            if overlay is None:
                overlay = build_agent_overlay_for_step15_prediction(prediction, hits, list(prediction.validation.get("critic_flags") or []))
            overlay = apply_addressed_evidence_gate(resolved, overlay)
            if self.sufficiency_enabled:
                overlay = apply_sufficiency_gate(prediction, overlay, hits)
            overlays[field_id] = overlay
            if overlay.review_required:
                review = make_step15_review_item(items_by_id.get(field_id, {}), prediction, overlay, hits)
                self.review_items = merge_review_items(self.review_items, [review] if review else [])
        self.agent_overlays = list(overlays.values())

    def acquisition_contract(self) -> dict[str, Any]:
        return build_acquisition_contract(
            self.config, prompt_version=self.prompt_version, collection_name=self.collection_name,
            layered_plan=self.layered_plan, allowed_layers=self.allowed_layers,
            overwrite_all_cli=self.overwrite_all_cli,
            index_scopes=self.index_scopes,
        )

    def check_sufficiency(self, item: dict[str, Any], hits: list[dict[str, Any]], decomposition: SlotDecomposition, *, retrieval_round: int) -> EvidenceSufficiency:
        messages = build_sufficiency_messages(
            item, hits, target_namespace=self.target_namespace, room_context=self.room_context,
            slot_schema=decomposition.to_prompt_dict(),
        )
        parse_error = None
        try:
            response = self.call_chat_with_retries(
                call_kind="sufficiency", field_id=field_id_for_item(item), caller=self.sufficiency_caller,
                kwargs={"messages": messages, "item": item, "hits": hits, "retrieval_round": retrieval_round},
            )
        except Exception as exc:
            if not is_json_parse_error(exc):
                raise
            response = None
            parse_error = {"code": "SUFFICIENCY_SCHEMA_INVALID", "reason": "json_parse_retries_exhausted", "detail": display_text(str(exc), 240)}
        result = normalize_sufficiency(response, hits, item=item, slot_schema=decomposition.to_prompt_dict())
        if parse_error is not None:
            result = replace(result, diagnostics=[*result.diagnostics, parse_error])
        self.trace.record(field_id_for_item(item), "evidence_sufficiency_checked", {
            "retrieval_round": retrieval_round, **result.to_dict(),
        })
        return result

    def collect_sufficient_evidence(self, item: dict[str, Any], query: str, decomposition: SlotDecomposition) -> tuple[Step15RetrievalResult, EvidenceSufficiency]:
        """One primary acquisition and, only for a gap, one supplement."""
        field_id = field_id_for_item(item)
        configured_layers = {str(spec["layer_name"]) for spec in self.layered_plan}
        primary_layers = [name for name in ("target_structured_fact", "target_table_detail") if name in configured_layers]
        supplementary_layers = [name for name in (
            "target_structured_fact", "target_table_detail", "target_text_detail", "global_detail", "global_intro",
        ) if name in configured_layers]
        retrieval_started = perf_counter_ms()
        schema_queries = [build_targeted_query(
            item, [slot.label], target_namespace=self.target_namespace, room_context=self.room_context,
        ) for slot in decomposition.slots if slot.required and slot.evidence_required] or [query]
        primary = self.retrieve(query, layer_names=primary_layers, schema_queries=schema_queries)
        retrieval_latency = perf_counter_ms() - retrieval_started
        primary_hits = tag_acquisition_hits(self.attach_parent_payloads(primary.reranked_hits), 0, [])
        primary_vectors = tag_acquisition_hits(self.attach_parent_payloads(primary.vector_hits), 0, [])
        rounds = [{"retrieval_round": 0, "query": query, "hit_count": len(primary_hits), **(primary.metadata or {})}]
        for selection in (primary.metadata or {}).get("schema_first", {}).get("selections", []):
            self.trace.record(field_id, "field_schema_selected", {"retrieval_round": 0, **selection})
        self.trace.record(field_id, "primary_retrieval_completed", rounds[0])
        sufficiency_started = perf_counter_ms()
        sufficient = self.check_sufficiency(item, primary_hits, decomposition, retrieval_round=0)
        sufficiency_latency = perf_counter_ms() - sufficiency_started
        hits, vectors = primary_hits, primary_vectors
        conflicts: list[dict[str, Any]] = []
        if not sufficient.sufficient:
            missing = sufficient.missing_facts
            supplement_query = build_targeted_query(item, missing, target_namespace=self.target_namespace, room_context=self.room_context)
            self.trace.record(field_id, "targeted_retrieval_started", {
                "retrieval_round": 1, "missing_facts": missing, "query": supplement_query,
            })
            retrieval_started = perf_counter_ms()
            supplement = self.retrieve(supplement_query, layer_names=supplementary_layers, schema_queries=[
                build_targeted_query(item, [fact], target_namespace=self.target_namespace, room_context=self.room_context)
                for fact in missing
            ])
            retrieval_latency += perf_counter_ms() - retrieval_started
            supplement_hits = tag_acquisition_hits(self.attach_parent_payloads(supplement.reranked_hits), 1, missing)
            supplement_vectors = tag_acquisition_hits(self.attach_parent_payloads(supplement.vector_hits), 1, missing)
            for selection in (supplement.metadata or {}).get("schema_first", {}).get("selections", []):
                self.trace.record(field_id, "field_schema_selected", {"retrieval_round": 1, **selection})
            before_ids = set(chunk_ids(primary_hits))
            hits, conflicts = merge_acquisition_hits(primary_hits, supplement_hits, target_namespace=self.target_namespace)
            vectors, _ = merge_acquisition_hits(primary_vectors, supplement_vectors, target_namespace=self.target_namespace)
            rounds.append({"retrieval_round": 1, "query": supplement_query, "missing_facts": missing,
                           "hit_count": len(supplement_hits), "evidence_gain": len(set(chunk_ids(hits)) - before_ids), **(supplement.metadata or {})})
            self.trace.record(field_id, "targeted_retrieval_completed", {**rounds[-1], "conflicting_evidence": conflicts})
            sufficiency_started = perf_counter_ms()
            sufficient = self.check_sufficiency(item, hits, decomposition, retrieval_round=1)
            sufficiency_latency += perf_counter_ms() - sufficiency_started
        if conflicts:
            sufficient = replace(sufficient, sufficient=False, missing_facts=sufficient.missing_facts or ["冲突来源的可核验事实"],
                                 reason="Same evidence ID has conflicting or invalid source origins", diagnostics=[*sufficient.diagnostics, *conflicts])
            self.trace.record(field_id, "evidence_sufficiency_conflict_blocked", sufficient.to_dict())
        backend_calls = [entry.get("qdrant_query_calls") for entry in rounds]
        metadata = {
            "strategy": "sufficiency_guided", "acquisition_rounds": len(rounds), "rounds": rounds,
            "qdrant_query_calls": sum(backend_calls) if all(isinstance(value, int) for value in backend_calls) else None,
            "retrieval_attempts": sum(int(entry.get("retrieval_attempts", 1)) for entry in rounds),
            "retrieval_latency_ms": round(retrieval_latency, 3), "sufficiency_latency_ms": round(sufficiency_latency, 3),
            "final_sufficiency": sufficient.to_dict(), "conflicting_evidence": conflicts,
        }
        return Step15RetrievalResult(reranked_hits=hits, vector_hits=vectors, retrieval_mode=self.retrieval_plan, metadata=metadata), sufficient

    def _process_item_original(self, item: dict[str, Any]) -> Step15FieldResult:
        field_id = field_id_for_item(item)
        self.trace.record(field_id, "field_started", {"field": minimal_item_view(item), "room_context": display_text(self.room_context)})

        base_query = build_masked_query(item, self.target_namespace)
        retrieval_query = add_room_context(base_query, self.room_context)
        self.trace.record(
            field_id,
            "query_planned",
            {
                "masked_query_preview": display_text(retrieval_query, 240),
                "masked_query": retrieval_query,
                "retrieval_query_preview": display_text(retrieval_query, 240),
                "retrieval_query": retrieval_query,
                "room_context": display_text(self.room_context),
            },
        )

        slot_decomposition = self.decompose_slots(item)
        self.trace.record(field_id, "slot_decomposed", slot_decomposition.to_dict())
        retrieval_started = perf_counter_ms()
        sufficiency = None
        if self.sufficiency_enabled:
            retrieval_result, sufficiency = self.collect_sufficient_evidence(item, retrieval_query, slot_decomposition)
        else:
            retrieval_result = self.retrieve(retrieval_query)
        retrieval_latency_ms = round(perf_counter_ms() - retrieval_started, 3)
        if self.sufficiency_enabled:
            retrieval_latency_ms = float((retrieval_result.metadata or {}).get("retrieval_latency_ms") or 0)
        top_hits = self.attach_parent_payloads(retrieval_result.reranked_hits)
        vector_hits = self.attach_parent_payloads(retrieval_result.vector_hits)
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
                "acquisition": retrieval_result.metadata or {},
            },
        )

        generation_started = perf_counter_ms()
        messages = build_qdrant_answer_messages(
            item,
            retrieval_query,
            top_hits,
            room_context=None,
            prompt_version=self.prompt_version,
            slot_schema=slot_decomposition.to_prompt_dict(),
        )
        if sufficiency is not None and not sufficiency.sufficient:
            # This is a system abstention before generation, not a rewritten LLM answer.
            generated = {
                "answer_value": "未找到", "answer_status": "partial_clue" if top_hits else "not_found",
                "confidence": 0.0, "source_chunk_ids": [], "evidence_attachment_ids": [],
                "reference_source_documents": [{"chunk_id": hit["chunk_id"], "quote": "", "reason": sufficiency.reason} for hit in top_hits if hit.get("chunk_id")],
                "missing_fields": sufficiency.missing_facts, "notes": sufficiency.reason,
                "origin": "system_sufficiency_abstention", "evidence_sufficiency": sufficiency.to_dict(),
            }
        else:
            generated = self.call_answer(messages=messages, item=item, query_text=retrieval_query, hits=top_hits)
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
                "origin": generated.get("origin", "model"),
            },
        )

        prediction = convert_step15_generated_to_prediction(item, generated, top_hits, retrieval_mode=self.retrieval_plan)
        if self.sufficiency_enabled:
            prediction = replace(prediction, validation={**prediction.validation, "acquisition": retrieval_result.metadata})
        prediction = attach_slot_validation(prediction, generated, slot_decomposition)
        critic_flags = critic_check_step15_answer(item, generated, top_hits)
        overlay = build_agent_overlay_for_step15_prediction(prediction, top_hits, critic_flags)
        overlay, grounding_result = self.apply_grounding_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            query_text=retrieval_query,
        )
        overlay, binding_agent_result = self.apply_field_binding_agent_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            grounding_result=grounding_result,
        )
        overlay, slot_result = self.apply_slot_consistency_overlay(
            item=item,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            overlay=overlay,
            decomposition=slot_decomposition,
        )
        critic_flags = overlay.critic_flags
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
                "field_binding_agent": binding_agent_result.to_dict(),
                "slot_consistency": slot_result.to_dict(),
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
            eval_result = make_eval_result(item, generated, judge, top_hits, vector_hits, retrieval_query, self.room_context)
            self.trace.record(
                field_id,
                "judge_completed",
                {"label": judge.get("label"), "score": judge.get("score"), "reason": judge.get("reason")},
            )

        return Step15FieldResult(
            item=item,
            masked_query=retrieval_query,
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
        retrieval_query = query_plan.query_text
        self.trace.record(
            field_id,
            "query_planned",
            {
                "masked_query_preview": display_text(retrieval_query, 240),
                "masked_query": retrieval_query,
                "retrieval_query_preview": display_text(retrieval_query, 240),
                "retrieval_query": retrieval_query,
                "room_context": display_text(self.room_context),
            },
        )

        retrieval = self.mas_controller.run_evidence_retrieval(item, retrieval_query)
        top_hits = self.attach_parent_payloads(retrieval.top_hits)
        vector_hits = self.attach_parent_payloads(retrieval.vector_hits)
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

        slot_decomposition = self.decompose_slots(item)
        self.trace.record(field_id, "slot_decomposed", slot_decomposition.to_dict())
        arbitration = self.mas_controller.run_answer_arbitration(item, retrieval_query, top_hits, slot_schema=slot_decomposition.to_prompt_dict())
        generated = arbitration.generated
        prediction = attach_slot_validation(arbitration.prediction, generated, slot_decomposition)
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
        overlay, grounding_result = self.apply_grounding_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            query_text=retrieval_query,
        )
        overlay, binding_agent_result = self.apply_field_binding_agent_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            grounding_result=grounding_result,
        )
        overlay, slot_result = self.apply_slot_consistency_overlay(
            item=item,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            overlay=overlay,
            decomposition=slot_decomposition,
        )
        critic_flags = overlay.critic_flags
        review_item = make_step15_review_item(item, prediction, overlay, top_hits)
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
                "evidence_strength": grounding_result.evidence_strength if grounding_result else None,
                "field_binding": grounding_result.field_binding if grounding_result else None,
                "field_binding_agent": binding_agent_result.to_dict(),
                "slot_consistency": slot_result.to_dict(),
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
            eval_result = make_eval_result(item, generated, judge, top_hits, vector_hits, retrieval_query, self.room_context)
            self.trace.record(
                field_id,
                "judge_completed",
                {"label": judge.get("label"), "score": judge.get("score"), "reason": judge.get("reason")},
            )

        return Step15FieldResult(
            item=item,
            masked_query=retrieval_query,
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

    def retrieve(
        self, query_text: str, *, layer_names: list[str] | None = None, source_types: list[str] | None = None,
        schema_queries: list[str] | None = None,
    ) -> Step15RetrievalResult:
        try:
            plan = constrain_layered_plan(self.layered_plan, layer_names=layer_names, source_types=source_types)
        except ValueError as exc:
            # A model-proposed invalid action must neither broaden retrieval nor
            # discard the field's original prediction by failing the whole run.
            reason = str(exc)
            self.trace.record(None, "retrieval_constraint_rejected", {"reason": reason})
            return Step15RetrievalResult(
                reranked_hits=[], vector_hits=[], retrieval_mode=self.retrieval_plan,
                metadata={"constraint_rejected": True, "reason": reason, "qdrant_query_calls": 0, "retrieval_attempts": 0},
            )
        if not plan:
            return Step15RetrievalResult(reranked_hits=[], vector_hits=[], retrieval_mode=self.retrieval_plan,
                                         metadata={"qdrant_query_calls": 0, "retrieval_attempts": 0})
        query_calls_before = getattr(self.retriever, "qdrant_query_calls", None)
        attempts = self.chat_max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                if self.retrieval_fn is not None:
                    result = self.retrieval_fn(query_text)
                    self.validate_pinned_hits(result.reranked_hits)
                    self.validate_pinned_hits(result.vector_hits)
                    if layer_names is not None or source_types is not None:
                        constraints = {
                            "layered_plan": plan,
                            "target_namespace": self.target_namespace,
                            "global_namespace": self.global_namespace,
                            "allowed_layers": self.allowed_layers,
                        }
                        result = replace(
                            result,
                            reranked_hits=filter_hits_by_plan(result.reranked_hits, **constraints),
                            vector_hits=filter_hits_by_plan(result.vector_hits, **constraints),
                        )
                else:
                    if self.retriever is None or self.reranker is None:
                        raise RuntimeError("Step15AgentRunner requires retriever and reranker")
                    result = run_step15_retrieval(
                        query_text,
                        retriever=self.retriever,
                        reranker=self.reranker,
                        target_namespace=self.target_namespace,
                        global_namespace=self.global_namespace,
                        allowed_layers=self.allowed_layers,
                        retrieval_mode=self.retrieval_plan,
                        vector_top_k=self.vector_top_k,
                        rerank_top_n=self.rerank_top_n,
                        layered_plan=plan,
                        schema_first_enabled=self.schema_first_enabled,
                        schema_queries=schema_queries,
                    )
            except Exception as exc:  # noqa: BLE001 - network retries wrap injected and real retrieval
                retryable = is_retryable_service_error(exc) or is_json_parse_error(exc)
                if not retryable or attempt >= attempts:
                    if retryable:
                        self.trace.record(
                            None,
                            "retrieval_retry_failed",
                            {
                                "attempt": attempt,
                                "max_retries": self.chat_max_retries,
                                "error": display_text(str(exc), 240),
                            },
                        )
                    raise
                self.trace.record(
                    None,
                    "retrieval_retry_started",
                    {
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
            self.validate_pinned_hits(result.reranked_hits)
            self.validate_pinned_hits(result.vector_hits)
            if attempt > 1:
                self.trace.record(None, "retrieval_retry_succeeded", {"attempt": attempt, "max_retries": self.chat_max_retries})
            query_calls_after = getattr(self.retriever, "qdrant_query_calls", None)
            metadata = {**(result.metadata or {}), "retrieval_attempts": attempt}
            if isinstance(query_calls_before, int) and isinstance(query_calls_after, int):
                metadata["qdrant_query_calls"] = query_calls_after - query_calls_before
            else:
                metadata.setdefault("qdrant_query_calls", None)
            return replace(result, metadata=metadata)
        raise RuntimeError("retrieval retry loop exited unexpectedly")

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
        caller: AnswerCaller | JudgeCaller | SlotDecomposerCaller | FieldBindingJudgeCaller | None,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        attempts = self.chat_max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                if caller is not None:
                    result = caller(**kwargs)
                else:
                    operation = "step15_answer"
                    if call_kind == "judge":
                        operation = "step15_judge"
                    elif call_kind == "slot_decomposition":
                        operation = "step15_slot_decomposition"
                    elif call_kind == "field_binding":
                        operation = "step15_field_binding"
                    elif call_kind == "sufficiency":
                        operation = "step15_sufficiency"
                    endpoint, headers = model_gateway.request_options(
                        model_gateway.KIND_CHAT,
                        self.chat_endpoint,
                        operation,
                        field_id=field_id,
                        direct_headers={"Authorization": f"Bearer {self.chat_api_key}"},
                    )
                    result = call_deepseek_json(
                        url=endpoint,
                        model=self.chat_model,
                        api_key=self.chat_api_key,
                        messages=kwargs["messages"],
                        timeout=self.timeout_seconds,
                        headers=headers,
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
                retryable = is_retryable_service_error(exc) or is_json_parse_error(exc)
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
            if prediction.field_id in predictions:
                raise RuntimeError(f"cannot resume: duplicate prediction identity: {prediction.field_id}")
            predictions[prediction.field_id] = prediction
        return predictions

    def load_checkpoint_sidecars(self) -> None:
        retrieval_checkpoint = self.out_dir / "retrieval_evidence.checkpoint.jsonl"
        if retrieval_checkpoint.is_file():
            for record in read_jsonl(retrieval_checkpoint):
                field_id = str(record.get("field_id") or "")
                hits = record.get("top_hits")
                if not field_id or field_id in self.retrieval_evidence_by_field_id or not isinstance(hits, list) or any(not isinstance(hit, dict) for hit in hits):
                    raise RuntimeError("cannot resume: malformed or duplicate retrieval authority")
                self.retrieval_evidence_by_field_id[field_id] = hits
                self.validate_pinned_hits(hits)
        if self.review_checkpoint_path().exists():
            self.review_items = read_jsonl(self.review_checkpoint_path())
        if self.eval_checkpoint_path().exists():
            self.eval_results = read_jsonl(self.eval_checkpoint_path())
        if self.overlay_checkpoint_path().exists():
            self.agent_overlays = [AgentOverlay.from_dict(record) for record in read_jsonl(self.overlay_checkpoint_path())]
        if self.evidence_checkpoint_path().exists():
            self.evidence_provenance_by_field_id = {
                str(record["field_id"]): record for record in read_jsonl(self.evidence_checkpoint_path()) if record.get("field_id")
            }
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
        write_jsonl(self.out_dir / "retrieval_evidence.checkpoint.jsonl", [
            {"field_id": prediction.field_id, "top_hits": self.retrieval_evidence_by_field_id.get(prediction.field_id, [])}
            for prediction in predictions
        ])
        write_jsonl(self.predictions_checkpoint_path(), [prediction.to_dict() for prediction in predictions])
        write_jsonl(self.overlay_checkpoint_path(), [overlay.to_dict() for overlay in overlays])
        write_jsonl(self.evidence_checkpoint_path(), self.evidence_fields_for_predictions(predictions, overlays))
        if self.judge_enabled:
            write_jsonl(self.eval_checkpoint_path(), ordered_eval_results_for_items(items, self.eval_results))
        write_jsonl(self.review_checkpoint_path(), self.review_items)
        self.trace.write_jsonl(self.trace_checkpoint_path())
        write_json(self.out_dir / "run_state.json", run_state)

    def attach_parent_payloads(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.validate_pinned_hits(hits)
        if not self.parent_payload_enabled:
            return hits
        return attach_parent_payloads(
            hits,
            max_chars=self.config.retrieval.parent_payload_max_chars,
            include_neighbors=self.config.retrieval.parent_payload_include_neighbors,
            neighbor_window=self.config.retrieval.parent_payload_neighbor_window,
            include_raw_parent_text=self.config.retrieval.parent_payload_include_raw_parent_text,
        )

    def validate_pinned_hits(self, hits: list[dict[str, Any]]) -> None:
        from nested_doc_rag.retrieval.version_scope import validate_hits_in_index_scopes
        validate_hits_in_index_scopes(hits, self.index_scopes, collection_name=self.collection_name,
                                     namespaces=[self.target_namespace, self.global_namespace] if self.index_scopes is not None else None)

    def apply_grounding_overlay(
        self,
        *,
        item: dict[str, Any],
        prediction: FieldPrediction,
        top_hits: list[dict[str, Any]],
        overlay: AgentOverlay,
        query_text: str,
    ) -> tuple[AgentOverlay, EvidenceStrengthResult | None]:
        if not self.grounding_enabled:
            return overlay, None

        field_id = field_id_for_item(item)
        grounding_result = EvidenceStrengthEvaluator(
            target_namespace=self.target_namespace,
            global_intro_answer_allowed=self.config.grounding.global_intro_answer_allowed,
            require_target_source_for_answered=self.config.grounding.require_target_source_for_answered,
            room_context=self.room_context,
            field_binding_enabled=self.field_binding_enabled,
        ).evaluate(item=item, prediction=prediction, top_hits=top_hits)
        grounded_overlay = apply_evidence_strength_to_overlay(
            prediction,
            overlay,
            grounding_result,
            min_strength_for_answered=self.config.grounding.min_strength_for_answered,
            min_strength_for_writeback=self.config.grounding.min_strength_for_writeback,
            downgrade_unsupported_answer_to_partial=self.config.grounding.downgrade_unsupported_answer_to_partial,
            relaxed_writeback_gate_enabled=self.relaxed_writeback_gate_enabled,
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
                        "review_required": grounded_overlay.review_required,
                        "writeback_allowed": grounded_overlay.writeback_allowed,
                        "risk_level": grounded_overlay.risk_level,
                        "reasons": grounded_overlay.reasons,
                    },
                }
            )
        return grounded_overlay, grounding_result

    def apply_field_binding_agent_overlay(
        self,
        *,
        item: dict[str, Any],
        prediction: FieldPrediction,
        top_hits: list[dict[str, Any]],
        overlay: AgentOverlay,
        grounding_result: EvidenceStrengthResult | None,
    ) -> tuple[AgentOverlay, FieldBindingAgentResult]:
        field_id = field_id_for_item(item)
        if (
            not self.field_binding_agent_enabled
            or prediction.answer_status != "answered"
            or not overlay.writeback_allowed
        ):
            result = FieldBindingAgentResult(
                checked=False,
                passed=True,
                label="skipped",
                reasons=["field_binding_agent_skipped"],
            )
            return overlay, result

        binding_hits = select_binding_hits(prediction, top_hits)
        evidence_chunk_ids = [str(hit.get("chunk_id")) for hit in binding_hits if hit.get("chunk_id")]
        if self.answer_caller is not None and self.field_binding_judge_caller is None:
            result = FieldBindingAgentResult(
                checked=False,
                passed=True,
                label="skipped",
                reasons=["field_binding_agent_skipped_for_injected_answer_caller"],
                evidence_chunk_ids=evidence_chunk_ids,
            )
            return overlay, result

        try:
            generated = self.call_field_binding_judge(
                messages=build_field_binding_messages(
                    item=item,
                    prediction=prediction,
                    hits=binding_hits,
                    room_context=self.room_context,
                    rule_binding=grounding_result.field_binding if grounding_result else None,
                ),
                item=item,
                prediction=prediction,
                hits=binding_hits,
                grounding_result=grounding_result.to_dict() if grounding_result else None,
            )
            result = normalize_field_binding_agent_result(generated, evidence_chunk_ids=evidence_chunk_ids)
        except Exception as exc:  # noqa: BLE001 - fail closed into review, but keep raw answer unchanged
            result = FieldBindingAgentResult(
                checked=True,
                passed=False,
                label="uncertain",
                confidence=0.0,
                reasons=["field_binding_agent_failed", display_text(str(exc), 240)],
                evidence_chunk_ids=evidence_chunk_ids,
            )
        self.trace.record(field_id, "field_binding_agent_checked", result.to_dict())
        if result.passed:
            return overlay, result

        critic_flags = dedupe([*overlay.critic_flags, result.label, "field_binding_agent_mismatch"])
        reasons = dedupe([*overlay.reasons, *result.reasons, result.label, "field_binding_agent_failed_writeback_check"])
        return (
            replace(
                overlay,
                critic_flags=critic_flags,
                review_required=True,
                writeback_allowed=False,
                risk_level=max_risk_level(overlay.risk_level, "high"),
                suggested_status=overlay.suggested_status or "partial_clue",
                suggested_answer_value=overlay.suggested_answer_value
                or "答案与引用证据的字段绑定不一致或无法确认；请人工复核。",
                reasons=reasons,
            ),
            result,
        )

    def call_field_binding_judge(self, **kwargs: Any) -> dict[str, Any]:
        return self.call_chat_with_retries(
            call_kind="field_binding",
            field_id=field_id_for_item(kwargs.get("item") or {}),
            caller=self.field_binding_judge_caller,
            kwargs=kwargs,
        )

    def decompose_slots(self, item: dict[str, Any]) -> SlotDecomposition:
        if not self.slot_decomposition_enabled:
            return EMPTY_DECOMPOSITION
        cache_key = slot_cache_key(item)
        if cache_key in self.slot_decomposition_cache:
            return self.slot_decomposition_cache[cache_key]

        field_id = field_id_for_item(item)
        if self.answer_caller is not None and self.slot_decomposer_caller is None:
            decomposition = heuristic_slot_decomposition(item)
            self.slot_decomposition_cache[cache_key] = decomposition
            return decomposition

        try:
            generated = self.call_slot_decomposition(messages=build_slot_decomposition_messages(item), item=item)
            decomposition = normalize_slot_decomposition(generated, item)
        except Exception as exc:  # noqa: BLE001 - slot decomposition is a safety helper, not a field fatal
            decomposition = heuristic_slot_decomposition(item)
            self.trace.record(
                field_id,
                "slot_decomposition_failed",
                {"error": display_text(str(exc), 240), "fallback": decomposition.to_dict()},
            )
        self.slot_decomposition_cache[cache_key] = decomposition
        return decomposition

    def call_slot_decomposition(self, **kwargs: Any) -> dict[str, Any]:
        return self.call_chat_with_retries(
            call_kind="slot_decomposition",
            field_id=field_id_for_item(kwargs.get("item") or {}),
            caller=self.slot_decomposer_caller,
            kwargs=kwargs,
        )

    def apply_slot_consistency_overlay(
        self,
        *,
        item: dict[str, Any],
        prediction: FieldPrediction,
        generated: dict[str, Any],
        top_hits: list[dict[str, Any]],
        overlay: AgentOverlay,
        decomposition: SlotDecomposition,
    ) -> tuple[AgentOverlay, SlotConsistencyResult]:
        field_id = field_id_for_item(item)
        if not self.pre_writeback_consistency_enabled:
            result = SlotConsistencyResult(
                checked=False,
                passed=True,
                flags=[],
                reasons=["pre_writeback_consistency_disabled"],
                decomposition=decomposition,
                slot_values=[],
            )
            return overlay, result

        result = evaluate_slot_consistency(
            item=item,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            decomposition=decomposition,
        )
        self.trace.record(field_id, "slot_consistency_checked", result.to_dict())
        if self.config.grounding.write_grounding_trace:
            self.slot_trace_records.append(
                {
                    "field_id": field_id,
                    "answer_status": prediction.answer_status,
                    "answer_value": prediction.answer_value,
                    "source_chunk_ids": prediction.source_chunk_ids,
                    **result.to_dict(),
                }
            )
        if result.passed or prediction.answer_status != "answered":
            return overlay, result

        critic_flags = dedupe([*overlay.critic_flags, *result.flags])
        reasons = dedupe([*overlay.reasons, *result.reasons, "pre_writeback_slot_consistency_failed"])
        return (
            replace(
                overlay,
                critic_flags=critic_flags,
                review_required=True,
                writeback_allowed=False,
                risk_level="high",
                suggested_status=overlay.suggested_status or "partial_clue",
                suggested_answer_value=overlay.suggested_answer_value
                or "答案缺少必需槽证据或槽值与证据不一致；请人工复核。",
                reasons=reasons,
            ),
            result,
        )

    def write_outputs(self, predictions: list[FieldPrediction], overlays: list[AgentOverlay], run_state: dict[str, Any]) -> None:
        authority_rows = [
            {"field_id": prediction.field_id, "top_hits": self.retrieval_evidence_by_field_id.get(prediction.field_id, [])}
            for prediction in predictions
        ]
        write_jsonl(self.out_dir / "retrieval_evidence.jsonl", authority_rows)
        write_jsonl(self.out_dir / "retrieval_evidence.checkpoint.jsonl", authority_rows)
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
        audit_records = (
            read_jsonl(self.out_dir / "writeback_audit.jsonl") if self.writeback_status == "completed" else []
        )
        # Custom writers may return field-level audits without writing a JSONL.
        if not audit_records and self.writeback_status == "completed" and self.writeback_summary:
            audit_records = list(self.writeback_summary.get("fields") or [])
        evidence = finalize_evidence(
            self.evidence_fields_for_predictions(predictions, overlays),
            audit_records=audit_records,
            writeback_status=self.writeback_status,
        )
        write_jsonl(self.out_dir / "evidence_provenance.jsonl", evidence["fields"])
        write_jsonl(self.evidence_checkpoint_path(), evidence["fields"])
        if self.mas_controller is not None:
            self.mas_controller.write_optional_artifacts(self.out_dir)
        if self.config.grounding.write_grounding_trace and self.grounding_trace_records:
            write_jsonl(self.out_dir / "grounding_trace.jsonl", self.grounding_trace_records)
        if self.config.grounding.write_grounding_trace and self.slot_trace_records:
            write_jsonl(self.out_dir / "slot_trace.jsonl", self.slot_trace_records)
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
        manifest["evidence"] = evidence
        manifest["writeback"]["config"] = asdict(self.config.writeback)
        manifest["writeback"]["overwrite_all_cli"] = self.overwrite_all_cli
        manifest["form_input"] = self.form_input_snapshot
        manifest["index_scopes"] = self.index_scopes
        manifest["artifacts"]["form_input_snapshot"] = "form_input_snapshot.json"
        if self.index_scopes is not None:
            manifest["artifacts"]["index_scopes"] = "index_scopes.json"
        for name in ("form_items.jsonl", "form_parse_report.json"):
            if (self.out_dir / name).is_file():
                manifest["artifacts"][name.split(".")[0]] = name
        manifest["artifacts"]["evidence_provenance"] = "evidence_provenance.jsonl"
        manifest["artifacts"]["retrieval_evidence"] = "retrieval_evidence.jsonl"
        if (self.out_dir / "mas_trace.jsonl").exists():
            manifest["artifacts"]["mas_trace"] = "mas_trace.jsonl"
        if (self.out_dir / "agentscope_events.jsonl").exists():
            manifest["artifacts"]["agentscope_events"] = "agentscope_events.jsonl"
        if (self.out_dir / "agentic_mas_trace.jsonl").exists():
            manifest["artifacts"]["agentic_mas_trace"] = "agentic_mas_trace.jsonl"
        if (self.out_dir / "agentic_round_states.jsonl").exists():
            manifest["artifacts"]["agentic_round_states"] = "agentic_round_states.jsonl"
        if (self.out_dir / "agentic_summary.json").exists():
            manifest["artifacts"]["agentic_summary"] = "agentic_summary.json"
        if (self.out_dir / "grounding_trace.jsonl").exists():
            manifest["artifacts"]["grounding_trace"] = "grounding_trace.jsonl"
        if (self.out_dir / "slot_trace.jsonl").exists():
            manifest["artifacts"]["slot_trace"] = "slot_trace.jsonl"
        if (self.out_dir / "image_evidence.jsonl").exists():
            manifest["artifacts"]["image_evidence"] = "image_evidence.jsonl"
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

        for hits in self.retrieval_evidence_by_field_id.values():
            self.validate_pinned_hits(hits)

        agent_review_items = list(self.review_items)
        overlay_by_field_id = {overlay.field_id: overlay for overlay in overlays}
        summary = self.writeback_fn(
            template_path=self.template_path,
            predictions=predictions,
            output_path=self.out_dir / "filled_form.xlsx",
            trace_by_field={prediction.field_id: f"{self.run_id}:{prediction.field_id}" for prediction in predictions},
            overlays_by_field_id=overlay_by_field_id,
            retrieval_hits_by_field_id=self.retrieval_evidence_by_field_id,
            writeback_config=self.config.writeback,
            overwrite_all_cli=self.overwrite_all_cli,
            run_id=self.run_id,
        )
        writeback_review_items = read_jsonl(self.out_dir / "review_items.jsonl")
        self.review_items = merge_review_items(agent_review_items, writeback_review_items)
        write_jsonl(self.out_dir / "review_items.jsonl", self.review_items)
        self.writeback_status = "completed"
        self.writeback_summary = summary.to_dict() if hasattr(summary, "to_dict") else dict(summary or {})

    def predictions_checkpoint_path(self) -> Path:
        return self.out_dir / "predictions.checkpoint.jsonl"

    def evidence_checkpoint_path(self) -> Path:
        return self.out_dir / "evidence_provenance.checkpoint.jsonl"

    def evidence_fields_for_predictions(
        self, predictions: list[FieldPrediction], overlays: list[AgentOverlay]
    ) -> list[dict[str, Any]]:
        overlays_by_id = {overlay.field_id: overlay for overlay in overlays}
        fields: list[dict[str, Any]] = []
        for prediction in predictions:
            field = self.evidence_provenance_by_field_id.get(prediction.field_id)
            if field is None:
                # Old checkpoints lack the original model references and hits.
                # Never upgrade auto-enriched previews to verifiable quotes.
                field = build_field_evidence(
                    item=self.evidence_items_by_field_id.get(prediction.field_id, {}),
                    prediction=prediction,
                    generated={},
                    top_hits=[],
                    overlay=overlays_by_id.get(prediction.field_id),
                    unavailable_reason="provenance_checkpoint_missing",
                )
                self.evidence_provenance_by_field_id[prediction.field_id] = field
            fields.append(field)
        return fields

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
            "fields_total": (self.form_input_snapshot or {}).get("selected_field_count", 0),
            "selected_field_count": (self.form_input_snapshot or {}).get("selected_field_count", 0),
            "form_input_fingerprint": (self.form_input_snapshot or {}).get("input_fingerprint"),
            "engine": "step15_agent",
            "target_namespace": self.target_namespace,
            "global_namespace": self.global_namespace,
            "room_context": display_text(self.room_context),
            "retrieval_plan": self.retrieval_plan,
            "retrieval_fusion_mode": "dense",
            "sufficiency_enabled": self.sufficiency_enabled,
            "acquisition_contract": self.acquisition_contract(),
            "grounding_enabled": self.grounding_enabled,
            "field_binding_enabled": self.field_binding_enabled,
            "field_binding_agent_enabled": self.field_binding_agent_enabled,
            "slot_decomposition_enabled": self.slot_decomposition_enabled,
            "pre_writeback_consistency_enabled": self.pre_writeback_consistency_enabled,
            "relaxed_writeback_gate_enabled": self.relaxed_writeback_gate_enabled,
            "parent_payload_enabled": self.parent_payload_enabled,
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
            "agentic_prompt_version": self.agentic_prompt_version,
            "use_judge_cache": self.use_judge_cache,
            "judge_cache_path": str(self.judge_cache_path) if self.judge_cache_path else "",
            "judge_enabled": self.judge_enabled,
            "writeback_enabled": self.writeback_enabled,
        }


def tag_acquisition_hits(hits: list[dict[str, Any]], retrieval_round: int, missing_facts: list[str]) -> list[dict[str, Any]]:
    return [{**hit, "retrieval_round": retrieval_round, "triggered_by": list(missing_facts)} for hit in hits]


def merge_acquisition_hits(primary: list[dict[str, Any]], supplement: list[dict[str, Any]], *, target_namespace: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deduplicate identity; retain an observable veto for differing origins."""
    merged: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for hit in [*primary, *supplement]:
        evidence_id = str(hit.get("evidence_id") or hit.get("chunk_id") or "")
        if not evidence_id:
            continue
        if evidence_id not in merged:
            merged[evidence_id] = dict(hit)
            continue
        previous = merged[evidence_id]
        resolved = resolve_evidence_refs([evidence_id], [previous, hit])
        if resolved.errors:
            conflicts.append({"code": "EV_REF_NOT_IN_RETRIEVAL", "chunk_id": evidence_id,
                              "reason": "conflicting_or_invalid_acquisition_origins", "errors": resolved.errors,
                              "origin_hits": [previous, hit]})
    kind_order = {"structured_field": 0, "table_row": 1, "paragraph": 2, "document_chunk": 3, "document_intro": 4}
    hits = sorted(merged.values(), key=lambda hit: (
        0 if hit.get("namespace") == target_namespace else 1,
        kind_order.get(str(hit.get("evidence_kind") or ""), 5),
        int(hit.get("layer_priority") or 99), int(hit.get("retrieval_round") or 0),
        int(hit.get("final_rank") or hit.get("rerank_rank") or hit.get("vector_rank") or 99),
    ))
    return hits, conflicts


def attach_addressed_evidence(prediction: FieldPrediction, top_hits: list[dict[str, Any]]) -> FieldPrediction:
    """Derive full references from the scoped retrieval pack, never answer JSON."""
    resolution = resolve_evidence_refs(prediction.source_chunk_ids, top_hits)
    errors = list(resolution.errors)
    if prediction.answer_status == "answered" and not resolution.refs:
        errors.append({"code": "EV_REF_MISSING_ADDRESS", "chunk_id": "", "reason": "answered_without_addressable_evidence"})
    attachment_ids = {value for ref in resolution.refs for value in ref.attachment_ids}
    for attachment_id in prediction.evidence_attachment_ids:
        if attachment_id not in attachment_ids:
            errors.append({"code": "EV_ATTACHMENT_NOT_FOUND", "chunk_id": "", "reason": "attachment_not_in_selected_evidence", "attachment_id": attachment_id})
    validation = {**prediction.validation, "addressable_evidence": {
        "resolvable": bool(resolution.refs) and not errors,
        "resolved_count": len(resolution.refs), "errors": errors,
    }}
    return replace(prediction, evidence_refs=resolution.refs, validation=validation)


def apply_addressed_evidence_gate(prediction: FieldPrediction, overlay: AgentOverlay) -> AgentOverlay:
    diagnostics = prediction.validation.get("addressable_evidence") or {}
    errors = diagnostics.get("errors") or []
    if prediction.answer_status != "answered" or diagnostics.get("resolvable"):
        return overlay
    flags = dedupe([*overlay.critic_flags, *(str(error["code"]) for error in errors), "addressable_evidence_required"])
    return replace(
        overlay, critic_flags=flags, review_required=True, writeback_allowed=False,
        risk_level="high", suggested_status=overlay.suggested_status or "partial_clue",
        suggested_answer_value=overlay.suggested_answer_value or "证据无法定位至已检索原文；请人工复核。",
        reasons=dedupe([*overlay.reasons, "addressable_evidence_required", *(str(error["code"]) for error in errors)]),
    )


def apply_sufficiency_gate(prediction: FieldPrediction, overlay: AgentOverlay, hits: list[dict[str, Any]]) -> AgentOverlay:
    if prediction.answer_status != "answered":
        return overlay
    acquisition = prediction.validation.get("acquisition") or {}
    final = acquisition.get("final_sufficiency") or {}
    check = normalize_sufficiency({key: final.get(key) for key in ("sufficient", "missing_facts", "supporting_evidence_ids", "reason")}, hits)
    if check.sufficient and not acquisition.get("conflicting_evidence"):
        return overlay
    return replace(overlay, writeback_allowed=False, review_required=True, risk_level="high",
                   critic_flags=dedupe([*overlay.critic_flags, "evidence_insufficient"]),
                   reasons=dedupe([*overlay.reasons, "evidence_insufficient"]),
                   suggested_status=overlay.suggested_status or "partial_clue",
                   suggested_answer_value=overlay.suggested_answer_value or "必要事实尚缺证据；请人工复核。")


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
    return attach_addressed_evidence(FieldPrediction(
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
    ), top_hits)


def attach_slot_validation(
    prediction: FieldPrediction,
    generated: dict[str, Any],
    decomposition: SlotDecomposition,
) -> FieldPrediction:
    if not decomposition.is_composite and not generated.get("slot_values"):
        return prediction
    validation = dict(prediction.validation)
    validation["slot_decomposition"] = decomposition.to_dict()
    validation["slot_values"] = [dict(item) for item in generated.get("slot_values") or [] if isinstance(item, dict)]
    return replace(prediction, validation=validation)


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
        "evidence_refs": [ref.to_dict() for ref in prediction.evidence_refs],
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


def parse_rows_arg(rows_text: str | None, *, step12_dir: Path | None = None) -> list[int] | None:
    text = (rows_text or "").strip()
    if not text or text.lower() == "all":
        return None
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
    if not rows or any(row < 1 for row in rows):
        raise ValueError("rows must contain positive Excel row numbers")
    return list(dict.fromkeys(rows))


def validate_step15_agent_config(
    *,
    qdrant_path: Path | None,
    qdrant_url: str = "",
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
            ("qdrant_path_or_url", qdrant_path or qdrant_url),
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
            "run-step15-agent requires qdrant_path or qdrant.url, collection_name, embedding_endpoint, embedding_model, rerank_endpoint, chat_endpoint, chat_model"
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
        hit = hits.get(chunk_id)
        reference = source_reference(hit, chunk_id=chunk_id)
        if hit is not None:
            address = EvidenceAddress.from_payload(hit).to_dict()
            reference.update({key: value for key, value in address.items() if value is not None})
            reference["cell"] = address["cell_range"] or reference["cell"]
        # Only selection, quote and explanation originate from the model.
        reference.update(reason=str(item.get("reason") or ""), quote=str(item.get("quote") or ""))
        output.append(reference)
    return output


def reference_source_documents_from_hits(top_hits: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for hit in top_hits[:limit]:
        chunk_id = str(hit.get("chunk_id") or "")
        if not chunk_id:
            continue
        source = hit_source(hit)
        docs.append(
            {
                "chunk_id": chunk_id,
                "namespace": hit.get("namespace"),
                "source_type": hit.get("source_type"),
                "corpus_layer": hit.get("corpus_layer"),
                "retrieval_layer": hit.get("retrieval_layer"),
                "source_anchor": hit.get("source_anchor") or hit.get("anchor"),
                "file_name": hit.get("file_name"),
                "relative_path": hit.get("relative_path") or source.get("relative_path"),
                "anchor": hit.get("anchor") or hit.get("source_anchor"),
                "document_id": source.get("document_id") or source.get("file_id") or hit.get("document_id"),
                "object_key": source.get("object_key") or hit.get("object_key"),
                "object_version_id": source.get("object_version_id") or "",
                "qdrant_point_id": hit.get("point_id") or hit.get("id"),
                "page": source.get("page"),
                "sheet_name": source.get("sheet_name"),
                "cell": source.get("cell") or source.get("cell_range"),
                "bbox": source.get("bbox") or [],
                "caption": source.get("caption") or "",
                "image_object_key": source.get("image_object_key") or "",
                "proof_attachment_ids": hit.get("proof_attachment_ids") or source.get("proof_attachment_ids") or [],
                "proof_attachments": hit.get("proof_attachments") or source.get("proof_attachments") or [],
                "reason": "retrieved related evidence, but not safe enough for direct filling",
                "text_preview": display_text(hit.get("raw_text") or hit.get("text_for_embedding"), 180),
            }
        )
    return docs


def hit_source(hit: Mapping[str, Any]) -> dict[str, Any]:
    source = hit.get("source")
    return dict(source) if isinstance(source, dict) else {}


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


def is_retryable_service_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in [
            "timeout",
            "timed out",
            "curl: (28)",
            "curl: (52)",
            "exit code 52",
            "empty reply from server",
            "operation timed out",
            "read timed out",
            "connection reset",
            "temporarily unavailable",
            "bad gateway",
            "502",
            "503",
            "504",
        ]
    )


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
        "field_id": field_id_for_item(item),
        "file_name": item.get("file_name"),
        "sheet_name": item.get("sheet_name"),
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
    field_binding_agent_labels = Counter(
        str(event.payload.get("label"))
        for event in events
        if event.step == "field_binding_agent_checked" and event.payload.get("checked")
    )
    field_binding_agent_flags = Counter(
        str(event.payload.get("label"))
        for event in events
        if event.step == "field_binding_agent_checked" and event.payload.get("checked") and not event.payload.get("passed")
    )
    slot_checks = Counter(
        "passed" if event.payload.get("passed") else "failed"
        for event in events
        if event.step == "slot_consistency_checked" and event.payload.get("checked")
    )
    slot_flags = Counter(
        flag
        for event in events
        if event.step == "slot_consistency_checked"
        for flag in event.payload.get("flags") or []
    )
    field_failed_errors = Counter(
        summarize_trace_error(event.payload.get("error"))
        for event in events
        if event.step == "field_failed" and event.payload.get("error")
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
        "field_failed_error_counts": dict(field_failed_errors.most_common(10)),
        "field_failed_error_examples": [
            {"error": error, "count": count}
            for error, count in field_failed_errors.most_common(10)
        ],
        "resumed_count": sum(1 for event in events if event.step == "resume_started"),
        "skipped_completed_count": sum(int(event.payload.get("skipped_completed_count") or 0) for event in events if event.step == "resume_started"),
        "evidence_strength_distribution": dict(evidence_strengths),
        "field_binding_distribution": dict(field_bindings),
        "field_binding_agent_distribution": dict(field_binding_agent_labels),
        "field_binding_agent_flag_counts": dict(field_binding_agent_flags),
        "slot_consistency_distribution": dict(slot_checks),
        "slot_consistency_flag_counts": dict(slot_flags),
        "retrieval_metrics": build_acquisition_metrics(predictions),
    }


def summarize_trace_error(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def build_acquisition_metrics(predictions: list[FieldPrediction]) -> dict[str, Any]:
    measurements = [prediction.validation["acquisition"] for prediction in predictions if isinstance(prediction.validation.get("acquisition"), dict)]
    measured = len(measurements)
    rounds = sum(int(record.get("acquisition_rounds") or 0) for record in measurements)
    supplemented = sum(int(record.get("acquisition_rounds") or 0) == 2 for record in measurements)
    calls = [record.get("qdrant_query_calls") for record in measurements]
    observed_calls = sum(calls) if measured and all(type(value) is int for value in calls) else None
    return {
        "total_fields": len(predictions), "measured_fields": measured,
        "acquisition_rounds": rounds if measured else None,
        "average_acquisition_rounds_per_measured_field": rounds / measured if measured else None,
        "second_round_trigger_rate": supplemented / measured if measured else None,
        "qdrant_query_calls": observed_calls,
        "average_qdrant_queries_per_measured_field": observed_calls / measured if observed_calls is not None else None,
        "evidence_gain": sum(int(entry.get("evidence_gain") or 0) for record in measurements for entry in record.get("rounds") or []) if measured else None,
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
        "retrieval_metrics": trace_summary.get("retrieval_metrics", {}),
        "field_binding_distribution": trace_summary.get("field_binding_distribution", {}),
        "field_binding_agent_distribution": trace_summary.get("field_binding_agent_distribution", {}),
        "field_binding_agent_flag_counts": trace_summary.get("field_binding_agent_flag_counts", {}),
        "slot_consistency_distribution": trace_summary.get("slot_consistency_distribution", {}),
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
    writeback_summary = summary.get("writeback_summary") or {}
    writeback_fields = writeback_summary.get("fields") or []
    writeback_block = {
        "summary": {
            "confirmed": int(writeback_summary.get("confirmed_count") or 0),
            "uncertain": int(writeback_summary.get("uncertain_count") or 0),
            "flagged": int(writeback_summary.get("flagged_count") or 0),
            "written": int(writeback_summary.get("written_count") or 0),
            "review": int(writeback_summary.get("review_count") or 0),
        },
        "fields": writeback_fields,
    }
    return {
        "schema_version": "1.3",
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
        "writeback": writeback_block,
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
    return ordered


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
    by_field = {str(result["field_id"]): result for result in eval_results if result.get("field_id")}
    legacy_by_row = {int(result.get("row_index") or 0): result for result in eval_results if not result.get("field_id")}
    return [result for item in items if (result := by_field.get(field_id_for_item(item)) or legacy_by_row.get(int(item.get("row_index") or 0))) is not None]


def ordered_eval_results_for_items_by_predictions(predictions: list[FieldPrediction], eval_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_field = {str(result["field_id"]): result for result in eval_results if result.get("field_id")}
    legacy_by_row = {int(result.get("row_index") or 0): result for result in eval_results if not result.get("field_id")}
    return [result for prediction in predictions if (result := by_field.get(prediction.field_id) or legacy_by_row.get(prediction.row_index)) is not None]


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
