from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult
from nested_doc_rag.schemas.eval import FieldPrediction


class EvidenceStateKind(StrEnum):
    SUFFICIENT = "sufficient"
    MISSING_INFO = "missing_info"
    WRONG_ANSWER_RISK = "wrong_answer_risk"
    NOT_FOUND_RECOVERY = "not_found_recovery"
    UNCERTAINTY_CONFLICT = "uncertainty_conflict"
    UNRESOLVED = "unresolved"


class FailureMode(StrEnum):
    SLOT_MISSING = "slot_missing"
    GRANULARITY_GAP = "granularity_gap"
    ENTITY_MISMATCH = "entity_mismatch"
    ATTRIBUTE_MISMATCH = "attribute_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    FORMAT_GAP = "format_gap"
    TEMPORAL_GAP = "temporal_gap"
    SOURCE_CONFLICT = "source_conflict"
    CANDIDATE_CONFLICT = "candidate_conflict"
    EVIDENCE_ABSENCE = "evidence_absence"
    WEAK_GROUNDING = "weak_grounding"


class ActionType(StrEnum):
    STOP = "stop"
    SLOT_TARGETED_RETRIEVAL = "slot_targeted_retrieval"
    ALIAS_RETRIEVAL = "alias_retrieval"
    LAYER_EXPANSION = "layer_expansion"
    SOURCE_SPECIFIC_RETRIEVAL = "source_specific_retrieval"
    CONTRASTIVE_RETRIEVAL = "contrastive_retrieval"
    DISAMBIGUATION_RETRIEVAL = "disambiguation_retrieval"
    MARK_UNRESOLVED = "mark_unresolved"


@dataclass(frozen=True)
class QueryPlanOutput:
    base_query: str
    query_text: str


@dataclass(frozen=True)
class EvidenceRetrievalOutput:
    retrieval_result: Step15RetrievalResult
    top_hits: list[dict[str, Any]]
    vector_hits: list[dict[str, Any]]
    retrieval_latency_ms: float


@dataclass(frozen=True)
class EvidenceCandidate:
    value: str
    supporting_chunk_ids: list[str]
    refuting_chunk_ids: list[str]
    scope: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "supporting_chunk_ids": self.supporting_chunk_ids,
            "refuting_chunk_ids": self.refuting_chunk_ids,
            "scope": self.scope,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EvidenceAction:
    action_type: ActionType
    query_text: str
    target_slot: str | None = None
    target_layer: str | None = None
    source_type_preference: str | None = None
    purpose: str | None = None
    semantic_invariant: dict[str, Any] | None = None
    expected_gain_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "query_text": self.query_text,
            "target_slot": self.target_slot,
            "target_layer": self.target_layer,
            "source_type_preference": self.source_type_preference,
            "purpose": self.purpose,
            "semantic_invariant": self.semantic_invariant,
            "expected_gain_type": self.expected_gain_type,
        }


@dataclass(frozen=True)
class EvidenceDiagnosis:
    state_kind: EvidenceStateKind
    failure_modes: list[FailureMode]
    sufficiency: str
    missing_information_need: str | None
    candidate_answers: list[EvidenceCandidate]
    risk_reason: str | None
    recommended_next_actions: list[EvidenceAction]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_kind": self.state_kind.value,
            "failure_modes": [mode.value for mode in self.failure_modes],
            "sufficiency": self.sufficiency,
            "missing_information_need": self.missing_information_need,
            "candidate_answers": [candidate.to_dict() for candidate in self.candidate_answers],
            "risk_reason": self.risk_reason,
            "recommended_next_actions": [action.to_dict() for action in self.recommended_next_actions],
        }


@dataclass
class AgenticMASState:
    item: dict[str, Any]
    base_query: str
    current_query: str
    round_index: int
    evidence: list[dict[str, Any]]
    vector_hits: list[dict[str, Any]]
    generated: dict[str, Any] | None
    prediction: FieldPrediction | None
    diagnosis: EvidenceDiagnosis | None
    actions_taken: list[EvidenceAction] = field(default_factory=list)
    stopped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "base_query": self.base_query,
            "current_query": self.current_query,
            "round_index": self.round_index,
            "evidence": self.evidence,
            "vector_hits": self.vector_hits,
            "generated": self.generated,
            "prediction": self.prediction.to_dict() if self.prediction is not None else None,
            "diagnosis": self.diagnosis.to_dict() if self.diagnosis is not None else None,
            "actions_taken": [action.to_dict() for action in self.actions_taken],
            "stopped_reason": self.stopped_reason,
        }


@dataclass(frozen=True)
class AnswerArbitrationOutput:
    generated: dict[str, Any]
    prediction: FieldPrediction
    generation_latency_ms: float
    diagnosis: EvidenceDiagnosis | None = None


@dataclass(frozen=True)
class OverlayControlOutput:
    critic_flags: list[str]
    overlay: Any
    review_item: dict[str, Any] | None


def parse_evidence_diagnosis(generated: Mapping[str, Any], top_hits: list[dict[str, Any]]) -> EvidenceDiagnosis:
    raw = generated.get("evidence_diagnosis")
    fallback = infer_evidence_diagnosis(generated, top_hits)
    if isinstance(raw, EvidenceDiagnosis):
        return raw
    if not isinstance(raw, Mapping):
        return fallback

    state_kind = parse_evidence_state_kind(raw.get("state_kind"), fallback=fallback.state_kind)
    failure_modes = [mode for item in raw.get("failure_modes") or [] if (mode := parse_failure_mode(item)) is not None]
    if not failure_modes:
        failure_modes = fallback.failure_modes
    candidates = [
        candidate
        for item in raw.get("candidate_answers") or []
        if isinstance(item, Mapping) and (candidate := parse_evidence_candidate(item)) is not None
    ]
    if not candidates:
        candidates = fallback.candidate_answers
    actions = [
        action
        for item in raw.get("recommended_next_actions") or []
        if isinstance(item, Mapping) and (action := parse_evidence_action(item)) is not None
    ]
    return EvidenceDiagnosis(
        state_kind=state_kind,
        failure_modes=failure_modes,
        sufficiency=str(raw.get("sufficiency") or fallback.sufficiency),
        missing_information_need=_optional_str(raw.get("missing_information_need")) or fallback.missing_information_need,
        candidate_answers=candidates,
        risk_reason=_optional_str(raw.get("risk_reason")) or fallback.risk_reason,
        recommended_next_actions=actions,
    )


def infer_evidence_diagnosis(generated: Mapping[str, Any], top_hits: list[dict[str, Any]]) -> EvidenceDiagnosis:
    status = str(generated.get("answer_status") or "not_found")
    confidence = _safe_float(generated.get("confidence"), default=0.0)
    source_chunk_ids = [str(chunk_id) for chunk_id in generated.get("source_chunk_ids") or [] if chunk_id]
    source_ids_valid = all(chunk_id in {str(hit.get("chunk_id")) for hit in top_hits if hit.get("chunk_id")} for chunk_id in source_chunk_ids)
    if status == "answered" and source_chunk_ids and source_ids_valid and confidence >= 0.7:
        state_kind = EvidenceStateKind.SUFFICIENT
        failure_modes: list[FailureMode] = []
        sufficiency = "sufficient"
        risk_reason = None
    elif status == "answered":
        state_kind = EvidenceStateKind.WRONG_ANSWER_RISK
        failure_modes = [FailureMode.WEAK_GROUNDING]
        sufficiency = "risky"
        risk_reason = "answered output has weak, missing, or invalid direct grounding"
    elif status == "partial_clue":
        state_kind = EvidenceStateKind.MISSING_INFO
        failure_modes = [FailureMode.SLOT_MISSING]
        sufficiency = "insufficient"
        risk_reason = None
    elif status == "not_found":
        state_kind = EvidenceStateKind.NOT_FOUND_RECOVERY
        failure_modes = [FailureMode.EVIDENCE_ABSENCE]
        sufficiency = "insufficient"
        risk_reason = None
    elif status == "conflict_unresolved":
        state_kind = EvidenceStateKind.UNCERTAINTY_CONFLICT
        failure_modes = [FailureMode.SOURCE_CONFLICT]
        sufficiency = "contradictory"
        risk_reason = "retrieved evidence could not be arbitrated deterministically"
    else:
        state_kind = EvidenceStateKind.UNRESOLVED
        failure_modes = [FailureMode.WEAK_GROUNDING]
        sufficiency = "insufficient"
        risk_reason = "unrecognized answer status"

    candidates: list[EvidenceCandidate] = []
    if status == "answered":
        candidates.append(
            EvidenceCandidate(
                value=str(generated.get("answer_value") or ""),
                supporting_chunk_ids=source_chunk_ids,
                refuting_chunk_ids=[],
                reason="fallback diagnosis from generated answer",
            )
        )
    missing_need = None
    missing_fields = [str(item) for item in generated.get("missing_fields") or [] if item]
    if missing_fields:
        missing_need = "；".join(missing_fields)
    elif status in {"partial_clue", "not_found"}:
        missing_need = "need more direct field-level evidence"
    return EvidenceDiagnosis(
        state_kind=state_kind,
        failure_modes=failure_modes,
        sufficiency=sufficiency,
        missing_information_need=missing_need,
        candidate_answers=candidates,
        risk_reason=risk_reason,
        recommended_next_actions=[],
    )


def parse_evidence_action(value: Mapping[str, Any]) -> EvidenceAction | None:
    action_type = parse_action_type(value.get("action_type"))
    if action_type is None:
        return None
    query_text = str(value.get("query_text") or "")
    semantic_invariant = value.get("semantic_invariant")
    return EvidenceAction(
        action_type=action_type,
        query_text=query_text,
        target_slot=_optional_str(value.get("target_slot")),
        target_layer=_optional_str(value.get("target_layer")),
        source_type_preference=_optional_str(value.get("source_type_preference")),
        purpose=_optional_str(value.get("purpose")),
        semantic_invariant=dict(semantic_invariant) if isinstance(semantic_invariant, Mapping) else None,
        expected_gain_type=_optional_str(value.get("expected_gain_type")),
    )


def parse_evidence_candidate(value: Mapping[str, Any]) -> EvidenceCandidate:
    return EvidenceCandidate(
        value=str(value.get("value") or ""),
        supporting_chunk_ids=[str(item) for item in value.get("supporting_chunk_ids") or [] if item],
        refuting_chunk_ids=[str(item) for item in value.get("refuting_chunk_ids") or [] if item],
        scope=_optional_str(value.get("scope")),
        reason=_optional_str(value.get("reason")),
    )


def parse_evidence_state_kind(value: Any, *, fallback: EvidenceStateKind = EvidenceStateKind.UNRESOLVED) -> EvidenceStateKind:
    text = str(value or "").strip()
    aliases = {
        "partial_clue": EvidenceStateKind.MISSING_INFO,
        "missing": EvidenceStateKind.MISSING_INFO,
        "not_found": EvidenceStateKind.NOT_FOUND_RECOVERY,
        "conflict": EvidenceStateKind.UNCERTAINTY_CONFLICT,
        "conflict_unresolved": EvidenceStateKind.UNCERTAINTY_CONFLICT,
        "risky": EvidenceStateKind.WRONG_ANSWER_RISK,
    }
    if text in aliases:
        return aliases[text]
    try:
        return EvidenceStateKind(text)
    except ValueError:
        return fallback


def parse_failure_mode(value: Any) -> FailureMode | None:
    try:
        return FailureMode(str(value or "").strip())
    except ValueError:
        return None


def parse_action_type(value: Any) -> ActionType | None:
    try:
        return ActionType(str(value or "").strip())
    except ValueError:
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
