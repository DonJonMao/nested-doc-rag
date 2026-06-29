from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any

from nested_doc_rag.io import display_text

STRENGTH_ORDER = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}
NUMBER_UNIT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:kva|kw|mw|w|g|gb|u|a|v|路|台|个|套)?", re.IGNORECASE)
YES_TERMS = {"是", "有", "支持", "满足", "具备", "已", "可以", "可"}
NO_TERMS = {"否", "无", "不支持", "不满足", "未", "没有", "不可"}
WEAK_ANSWER_VALUES = {"", "未找到", "无", "n/a", "na", "none", "null"}
FIELD_KEY_TERMS = {"ups", "pdu", "kva", "kw", "机柜", "u位", "容量", "数量", "地址", "端口", "链路", "a/b", "a路", "b路"}
ASCII_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[/*.-][a-z0-9]+)*(?:[a-z]+)?", re.IGNORECASE)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
SUPPORTED_BINDINGS = {"exact", "parent_exact"}
FIELD_BINDING_RISK = {
    "field_mismatch": "high",
    "scope_mismatch": "high",
    "status_mismatch": "high",
    "slot_mismatch": "high",
    "answer_evidence_mismatch": "high",
    "unit_mismatch": "medium",
}
HARD_BLOCK_BINDINGS = set(FIELD_BINDING_RISK)
HARD_STRENGTH_REASONS = {"missing_numeric_or_unit_support"}
PLANNED_TERMS = {"规划", "计划", "未来", "拟建", "待建", "改造", "扩容", "设计", "目标", "建设中", "条件", "可支持"}
CURRENT_TERMS = {"当前", "现网", "现有", "已建设", "已建", "已支持", "实际", "运行", "投产", "已投产", "生产"}
CONDITIONAL_TERMS = {"条件", "具备", "可支持", "可接入", "预留", "改造"}
NUMERIC_FIELD_TERMS = {"数量", "容量", "功率", "台数", "个数", "路数", "面积", "尺寸", "u位", "冗余", "配置"}
BOOLEAN_FIELD_TERMS = {"是否", "有无", "能否", "支持", "满足", "具备"}
UNIT_ALIASES = {
    "kva": "kva",
    "kvA": "kva",
    "kw": "kw",
    "mw": "mw",
    "w": "w",
    "路": "路",
    "台": "台",
    "个": "个",
    "套": "套",
    "u": "u",
    "u位": "u",
}
UNIT_RE = re.compile(r"(?:(?<=\d)\s*(kva|kw|mw|w|u)\b|\b(kva|kw|mw|w|u)\b|u位|路|台|个|套)", re.IGNORECASE)


@dataclass(frozen=True)
class FieldIntent:
    kind: str
    entity_terms: list[str]
    metric_terms: list[str]
    status_terms: list[str]
    scope_terms: list[str]
    unit_terms: list[str]
    negation_sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entity_terms": self.entity_terms,
            "metric_terms": self.metric_terms,
            "status_terms": self.status_terms,
            "scope_terms": self.scope_terms,
            "unit_terms": self.unit_terms,
            "negation_sensitive": self.negation_sensitive,
        }


@dataclass(frozen=True)
class FieldBindingResult:
    label: str
    score: float
    reasons: list[str]
    field_intent: str | None = None
    evidence_field_path: str | None = None
    expected_scope: str | None = None
    evidence_scope: str | None = None
    expected_status: str | None = None
    evidence_status: str | None = None
    expected_unit: str | None = None
    evidence_unit: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_details(self) -> dict[str, Any]:
        return {
            **self.details,
            "field_intent": self.field_intent,
            "evidence_field_path": self.evidence_field_path,
            "expected_scope": self.expected_scope,
            "evidence_scope": self.evidence_scope,
            "expected_status": self.expected_status,
            "evidence_status": self.evidence_status,
            "expected_unit": self.expected_unit,
            "evidence_unit": self.evidence_unit,
        }


@dataclass(frozen=True)
class EvidenceStrengthResult:
    evidence_strength: str
    reasons: list[str]
    valid_source_chunk_ids: list[str]
    invalid_source_chunk_ids: list[str]
    cited_hit_ids: list[str]
    matched_answer_tokens: list[str]
    missing_answer_tokens: list[str]
    field_binding: str = "exact"
    field_binding_score: float = 1.0
    field_binding_reasons: list[str] = field(default_factory=list)
    field_binding_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        details = dict(self.field_binding_details or {})
        return {
            "evidence_strength": self.evidence_strength,
            "strength_reasons": self.reasons,
            "valid_source_chunk_ids": self.valid_source_chunk_ids,
            "invalid_source_chunk_ids": self.invalid_source_chunk_ids,
            "cited_hit_ids": self.cited_hit_ids,
            "matched_answer_tokens": self.matched_answer_tokens,
            "missing_answer_tokens": self.missing_answer_tokens,
            "field_binding": self.field_binding,
            "field_binding_score": self.field_binding_score,
            "field_binding_reasons": self.field_binding_reasons,
            "field_binding_details": details,
            "field_intent": details.get("field_intent"),
            "evidence_field_path": details.get("evidence_field_path"),
            "expected_scope": details.get("expected_scope"),
            "evidence_scope": details.get("evidence_scope"),
            "expected_status": details.get("expected_status"),
            "evidence_status": details.get("evidence_status"),
            "expected_unit": details.get("expected_unit"),
            "evidence_unit": details.get("evidence_unit"),
        }


class EvidenceStrengthEvaluator:
    def __init__(
        self,
        *,
        target_namespace: str,
        global_intro_answer_allowed: bool = False,
        require_target_source_for_answered: bool = True,
        room_context: str | None = None,
        field_binding_enabled: bool = True,
    ) -> None:
        self.target_namespace = target_namespace
        self.global_intro_answer_allowed = global_intro_answer_allowed
        self.require_target_source_for_answered = require_target_source_for_answered
        self.room_context = room_context
        self.field_binding_enabled = field_binding_enabled

    def evaluate(self, *, item: dict[str, Any], prediction: Any, top_hits: list[dict[str, Any]]) -> EvidenceStrengthResult:
        status = str(getattr(prediction, "answer_status", "") or "")
        answer_value = display_text(getattr(prediction, "answer_value", ""))
        source_chunk_ids = [str(chunk_id) for chunk_id in getattr(prediction, "source_chunk_ids", []) or [] if chunk_id]
        hit_by_id = {str(hit.get("chunk_id")): hit for hit in top_hits if hit.get("chunk_id")}
        valid_source_chunk_ids = [chunk_id for chunk_id in source_chunk_ids if chunk_id in hit_by_id]
        invalid_source_chunk_ids = [chunk_id for chunk_id in source_chunk_ids if chunk_id not in hit_by_id]
        reasons: list[str] = []
        if status != "answered":
            binding = self.evaluate_binding(item=item, prediction=prediction, cited_hits=[hit_by_id[chunk_id] for chunk_id in valid_source_chunk_ids])
            if status == "partial_clue":
                reasons.append("partial_clue_status_preserved")
            return EvidenceStrengthResult(
                evidence_strength="E0" if not valid_source_chunk_ids else "E1",
                reasons=reasons or ["not_answered_status"],
                valid_source_chunk_ids=valid_source_chunk_ids,
                invalid_source_chunk_ids=invalid_source_chunk_ids,
                cited_hit_ids=valid_source_chunk_ids,
                matched_answer_tokens=[],
                missing_answer_tokens=[],
                field_binding=binding.label,
                field_binding_score=binding.score,
                field_binding_reasons=binding.reasons,
                field_binding_details=binding.to_details(),
            )
        if not source_chunk_ids:
            binding = self.unsupported_binding(item=item, prediction=prediction)
            return EvidenceStrengthResult(
                "E0",
                ["no_source_chunk_ids"],
                [],
                [],
                [],
                [],
                [],
                field_binding=binding.label,
                field_binding_score=binding.score,
                field_binding_reasons=binding.reasons,
                field_binding_details=binding.to_details(),
            )
        if invalid_source_chunk_ids:
            reasons.append("cited_source_not_in_retrieved_hits")
        if not valid_source_chunk_ids:
            binding = self.unsupported_binding(item=item, prediction=prediction)
            return EvidenceStrengthResult(
                "E0",
                dedupe([*reasons, "no_valid_evidence_support"]),
                [],
                invalid_source_chunk_ids,
                [],
                [],
                core_answer_tokens(answer_value),
                field_binding=binding.label,
                field_binding_score=binding.score,
                field_binding_reasons=binding.reasons,
                field_binding_details=binding.to_details(),
            )

        cited_hits = [hit_by_id[chunk_id] for chunk_id in valid_source_chunk_ids]
        binding = self.evaluate_binding(item=item, prediction=prediction, cited_hits=cited_hits)
        evidence_text = normalize_support_text(" ".join(display_text(hit.get("raw_text") or hit.get("text_for_embedding")) for hit in cited_hits))
        answer_tokens = core_answer_tokens(answer_value)
        matched_tokens, missing_tokens = partition_tokens(answer_tokens, evidence_text)
        question_text = display_text(item.get("question_text"))
        related = field_related(question_text, cited_hits)
        has_target_source = any(str(hit.get("namespace") or "") == self.target_namespace for hit in cited_hits)
        global_intro_only = all(is_global_intro_hit(hit) for hit in cited_hits)
        exact_source = any(is_exact_structured_hit(hit, self.target_namespace) for hit in cited_hits)
        numeric_tokens = numeric_answer_tokens(answer_value)
        numeric_supported = all(normalize_support_text(token) in evidence_text for token in numeric_tokens) if numeric_tokens else True
        boolean_supported = boolean_answer_supported(question_text, answer_value, evidence_text)

        if global_intro_only and not self.global_intro_answer_allowed:
            reasons.append("global_intro_only")
        if self.require_target_source_for_answered and not has_target_source:
            reasons.append("no_target_namespace_source")
        if numeric_tokens and not numeric_supported:
            reasons.append("missing_numeric_or_unit_support")
        if is_boolean_field(question_text) and not boolean_supported:
            reasons.append("missing_explicit_boolean_support")
        if missing_tokens:
            reasons.append("answer_value_tokens_missing_from_evidence")
        if matched_tokens:
            reasons.append("answer_value_tokens_found_in_evidence")
        if related:
            reasons.append("field_related_source")

        if global_intro_only and not self.global_intro_answer_allowed:
            strength = "E2" if matched_tokens and numeric_supported and boolean_supported else "E1"
        elif (self.require_target_source_for_answered and not has_target_source) or not numeric_supported or not boolean_supported:
            strength = "E2" if related or matched_tokens else "E1"
        elif matched_tokens and not missing_tokens and numeric_supported and boolean_supported:
            strength = "E4" if exact_source else "E3"
        elif matched_tokens and numeric_supported and boolean_supported:
            strength = "E3" if contains_field_key_token(answer_tokens) else "E2"
        elif related:
            strength = "E2"
        else:
            strength = "E1"

        return EvidenceStrengthResult(
            evidence_strength=strength,
            reasons=dedupe(reasons),
            valid_source_chunk_ids=valid_source_chunk_ids,
            invalid_source_chunk_ids=invalid_source_chunk_ids,
            cited_hit_ids=valid_source_chunk_ids,
            matched_answer_tokens=matched_tokens,
            missing_answer_tokens=missing_tokens,
            field_binding=binding.label,
            field_binding_score=binding.score,
            field_binding_reasons=binding.reasons,
            field_binding_details=binding.to_details(),
        )

    def evaluate_binding(self, *, item: dict[str, Any], prediction: Any, cited_hits: list[dict[str, Any]]) -> FieldBindingResult:
        if not self.field_binding_enabled:
            return disabled_field_binding(item=item, prediction=prediction, target_namespace=self.target_namespace, room_context=self.room_context)
        return evaluate_field_binding(
            item=item,
            prediction=prediction,
            cited_hits=cited_hits,
            target_namespace=self.target_namespace,
            room_context=self.room_context,
        )

    def unsupported_binding(self, *, item: dict[str, Any], prediction: Any) -> FieldBindingResult:
        if not self.field_binding_enabled:
            return disabled_field_binding(item=item, prediction=prediction, target_namespace=self.target_namespace, room_context=self.room_context)
        return unsupported_field_binding(item=item, prediction=prediction, target_namespace=self.target_namespace, room_context=self.room_context)


def apply_evidence_strength_to_overlay(
    prediction: Any,
    overlay: Any,
    evidence: EvidenceStrengthResult,
    *,
    min_strength_for_answered: str,
    min_strength_for_writeback: str,
    downgrade_unsupported_answer_to_partial: bool,
) -> Any:
    if getattr(prediction, "answer_status", None) != "answered":
        return overlay
    required_answer_rank = strength_rank(min_strength_for_answered)
    required_writeback_rank = strength_rank(min_strength_for_writeback)
    current_rank = strength_rank(evidence.evidence_strength)
    reasons = list(getattr(overlay, "reasons", []) or [])
    critic_flags = list(getattr(overlay, "critic_flags", []) or [])
    suggested_status = getattr(overlay, "suggested_status", None)
    suggested_answer_value = getattr(overlay, "suggested_answer_value", None)
    review_required = bool(getattr(overlay, "review_required", False))
    writeback_allowed = bool(getattr(overlay, "writeback_allowed", False))
    risk_level = str(getattr(overlay, "risk_level", "low") or "low")
    global_intro_only = "global_intro_only" in evidence.reasons
    field_binding = str(evidence.field_binding or "unsupported")

    if current_rank < required_answer_rank:
        reasons.append("evidence_strength_below_answer_threshold")
        if downgrade_unsupported_answer_to_partial:
            review_required = True
            writeback_allowed = False
            risk_level = max_risk_level(risk_level, "medium")
            critic_flags.append("unsupported_by_strong_evidence")
            suggested_status = "partial_clue"
            suggested_answer_value = "检索到相关线索，但证据强度不足以安全直接填写；请人工复核。"
    if current_rank < required_writeback_rank:
        reasons.append("evidence_strength_below_writeback_threshold")
    if global_intro_only:
        review_required = True
        writeback_allowed = False
        risk_level = max_risk_level(risk_level, "medium")
        reasons.append("global_intro_only")
    if evidence.evidence_strength == "E0":
        writeback_allowed = False
        review_required = True
        risk_level = "high"
        reasons.append("no_valid_evidence_support")
        critic_flags.append("no_valid_evidence_support")
    if HARD_STRENGTH_REASONS.intersection(evidence.reasons):
        writeback_allowed = False
        review_required = True
        risk_level = max_risk_level(risk_level, "high")
        reasons.append("answer_evidence_mismatch")
        critic_flags.append("answer_evidence_mismatch")
    if field_binding != "disabled" and field_binding not in SUPPORTED_BINDINGS:
        reasons.append("field_binding_not_exact")
        if field_binding in HARD_BLOCK_BINDINGS:
            writeback_allowed = False
            review_required = True
            reasons.append(field_binding)
            critic_flags.append(field_binding)
            risk_level = max_risk_level(risk_level, FIELD_BINDING_RISK.get(field_binding, "medium"))

    return replace(
        overlay,
        critic_flags=dedupe(critic_flags),
        review_required=review_required,
        writeback_allowed=writeback_allowed,
        suggested_status=suggested_status,
        suggested_answer_value=suggested_answer_value,
        risk_level=risk_level,
        reasons=dedupe(reasons),
    )


def unsupported_field_binding(
    *,
    item: dict[str, Any],
    prediction: Any,
    target_namespace: str,
    room_context: str | None,
) -> FieldBindingResult:
    intent = parse_field_intent(item=item, answer_value=getattr(prediction, "answer_value", ""), room_context=room_context)
    expected_scope = infer_expected_scope(item=item, room_context=room_context, target_namespace=target_namespace)
    return FieldBindingResult(
        label="unsupported",
        score=0.0,
        reasons=["no_legal_field_path"],
        field_intent=field_intent_summary(intent),
        expected_scope=expected_scope,
        details={"field_intent": intent.to_dict(), "expected_scope": expected_scope},
    )


def disabled_field_binding(
    *,
    item: dict[str, Any],
    prediction: Any,
    target_namespace: str,
    room_context: str | None,
) -> FieldBindingResult:
    intent = parse_field_intent(item=item, answer_value=getattr(prediction, "answer_value", ""), room_context=room_context)
    expected_scope = infer_expected_scope(item=item, room_context=room_context, target_namespace=target_namespace)
    return FieldBindingResult(
        label="disabled",
        score=0.0,
        reasons=["field_binding_disabled"],
        field_intent=field_intent_summary(intent),
        expected_scope=expected_scope,
        details={"field_intent": intent.to_dict(), "expected_scope": expected_scope, "field_binding_enabled": False},
    )


def evaluate_field_binding(
    *,
    item: dict[str, Any],
    prediction: Any,
    cited_hits: list[dict[str, Any]],
    target_namespace: str,
    room_context: str | None,
) -> FieldBindingResult:
    answer_value = display_text(getattr(prediction, "answer_value", ""))
    intent = parse_field_intent(item=item, answer_value=answer_value, room_context=room_context)
    expected_scope = infer_expected_scope(item=item, room_context=room_context, target_namespace=target_namespace)
    expected_status = infer_expected_status(intent)
    expected_units = dedupe([*intent.unit_terms, *extract_units(answer_value)])
    path_parts = evidence_field_path_parts(cited_hits)
    parent_path_parts = parent_payload_path_parts(cited_hits)
    evidence_field_path = " / ".join(path_parts)
    parent_field_path = " / ".join(parent_path_parts)
    path_text = normalize_support_text(" ".join(path_parts))
    parent_text = normalize_support_text(" ".join(parent_path_parts))
    evidence_text = normalize_support_text(
        " ".join(
            display_text(hit.get("raw_text") or hit.get("text_for_embedding"))
            for hit in cited_hits
        )
    )
    evidence_context = f"{evidence_field_path} {parent_field_path} {evidence_text}"
    match_text = " ".join(part for part in [path_text, parent_text, evidence_text] if part)
    evidence_scope = infer_evidence_scope(cited_hits=cited_hits, target_namespace=target_namespace, text=evidence_context)
    evidence_status = infer_evidence_status(evidence_context)
    evidence_units = dedupe([*extract_units(evidence_field_path), *extract_units(parent_field_path), *extract_units(evidence_text)])
    expected_unit = ",".join(expected_units) or None
    evidence_unit = ",".join(evidence_units) or None
    entity_matches = matched_terms(intent.entity_terms, match_text)
    metric_matches = matched_terms(intent.metric_terms, match_text)
    parent_entity_matches = matched_terms(intent.entity_terms, parent_text)
    parent_metric_matches = matched_terms(intent.metric_terms, parent_text)
    has_entity_intent = bool(intent.entity_terms)
    has_metric_intent = bool(intent.metric_terms)
    entity_ok = not has_entity_intent or bool(entity_matches)
    metric_ok = not has_metric_intent or bool(metric_matches)
    parent_entity_ok = not has_entity_intent or bool(parent_entity_matches)
    parent_metric_ok = not has_metric_intent or bool(parent_metric_matches)
    reasons: list[str] = []
    details = {
        "field_intent": intent.to_dict(),
        "evidence_field_path": evidence_field_path or None,
        "parent_field_path": parent_field_path or None,
        "expected_scope": expected_scope,
        "evidence_scope": evidence_scope,
        "expected_status": expected_status,
        "evidence_status": evidence_status,
        "expected_unit": expected_unit,
        "evidence_unit": evidence_unit,
        "matched_entity_terms": entity_matches,
        "matched_metric_terms": metric_matches,
        "matched_parent_entity_terms": parent_entity_matches,
        "matched_parent_metric_terms": parent_metric_matches,
    }
    specific_mismatch_reasons = field_specific_mismatch_reasons(
        question_text=display_text(item.get("question_text")),
        instruction_text=display_text(item.get("instruction_text")),
        category_path=item.get("category_path") or [],
        answer_value=answer_value,
        evidence_context=evidence_context,
    )
    slot_mismatch_reasons = combo_slot_mismatch_reasons(item=item, answer_value=answer_value, evidence_context=evidence_context)
    if specific_mismatch_reasons:
        details["answer_evidence_consistency_reasons"] = specific_mismatch_reasons
    if slot_mismatch_reasons:
        details["slot_mismatch_reasons"] = slot_mismatch_reasons
    if not cited_hits:
        return FieldBindingResult(
            "unsupported",
            0.0,
            ["no_cited_hits_for_binding"],
            field_intent=field_intent_summary(intent),
            evidence_field_path=None,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if not evidence_field_path and not evidence_text:
        return FieldBindingResult(
            "unsupported",
            0.0,
            ["no_legal_field_path"],
            field_intent=field_intent_summary(intent),
            evidence_field_path=None,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if specific_mismatch_reasons:
        return FieldBindingResult(
            "field_mismatch",
            0.1,
            specific_mismatch_reasons,
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if slot_mismatch_reasons:
        return FieldBindingResult(
            "slot_mismatch",
            0.1,
            slot_mismatch_reasons,
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if expected_scope == "target" and evidence_scope != "target":
        return FieldBindingResult(
            "scope_mismatch",
            0.1,
            ["evidence_scope_not_target"],
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if units_conflict(expected_units, evidence_units):
        return FieldBindingResult(
            "unit_mismatch",
            0.2,
            ["answer_unit_conflicts_with_evidence_unit"],
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if expected_units and not evidence_units and intent.kind == "numeric":
        return FieldBindingResult(
            "near",
            0.55,
            ["expected_unit_missing_from_field_path"],
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if status_conflicts(expected_status, evidence_status):
        label = "status_mismatch" if intent.kind == "boolean" or evidence_status == "conditional" else "field_mismatch"
        reason = "boolean_or_condition_status_mismatch" if label == "status_mismatch" else "numeric_field_status_mismatch"
        return FieldBindingResult(
            label,
            0.2,
            [reason],
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if has_entity_intent and not entity_ok:
        return FieldBindingResult(
            "near",
            0.55,
            ["entity_terms_not_bound_to_evidence_field"],
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if has_metric_intent and not metric_ok:
        if entity_ok:
            return FieldBindingResult(
                "near",
                0.55,
                ["entity_matched_but_metric_missing"],
                field_intent=field_intent_summary(intent),
                evidence_field_path=evidence_field_path,
                expected_scope=expected_scope,
                evidence_scope=evidence_scope,
                expected_status=expected_status,
                evidence_status=evidence_status,
                expected_unit=expected_unit,
                evidence_unit=evidence_unit,
                details=details,
            )
        return FieldBindingResult(
            "field_mismatch",
            0.2,
            ["metric_terms_not_bound_to_evidence_field"],
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if parent_path_parts and parent_entity_ok and parent_metric_ok and raw_hit_text_is_short(cited_hits):
        reasons.append("parent_payload_binds_short_hit")
        return FieldBindingResult(
            "parent_exact",
            0.95,
            reasons,
            field_intent=field_intent_summary(intent),
            evidence_field_path=parent_field_path or evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    if entity_ok and metric_ok:
        reasons.append("field_path_matches_intent")
        return FieldBindingResult(
            "exact",
            1.0,
            reasons,
            field_intent=field_intent_summary(intent),
            evidence_field_path=evidence_field_path,
            expected_scope=expected_scope,
            evidence_scope=evidence_scope,
            expected_status=expected_status,
            evidence_status=evidence_status,
            expected_unit=expected_unit,
            evidence_unit=evidence_unit,
            details=details,
        )
    return FieldBindingResult(
        "unsupported",
        0.0,
        ["unable_to_bind_field_intent"],
        field_intent=field_intent_summary(intent),
        evidence_field_path=evidence_field_path,
        expected_scope=expected_scope,
        evidence_scope=evidence_scope,
        expected_status=expected_status,
        evidence_status=evidence_status,
        expected_unit=expected_unit,
        evidence_unit=evidence_unit,
        details=details,
    )


def field_specific_mismatch_reasons(
    *,
    question_text: str,
    instruction_text: str,
    category_path: list[Any],
    answer_value: Any,
    evidence_context: str,
) -> list[str]:
    field_text = normalize_support_text(
        " ".join([question_text, instruction_text, " ".join(display_text(part) for part in category_path)])
    )
    evidence_text = normalize_support_text(evidence_context)
    answer_text = normalize_support_text(display_text(answer_value))
    reasons: list[str] = []

    if asks_oil_parallel_control(field_text) and not has_oil_parallel_control_terms(evidence_text):
        if has_oil_route_control_terms(evidence_text):
            reasons.append("oil_parallel_control_confused_with_oil_route_control")
        else:
            reasons.append("oil_parallel_control_terms_missing_from_evidence")
    elif asks_oil_equipment(field_text) and has_non_oil_power_system_terms(evidence_text) and not has_oil_equipment_terms(evidence_text):
        reasons.append("oil_machine_field_cited_non_oil_power_source")

    if asks_warehouse_cctv(field_text) and has_monitoring_terms(evidence_text) and has_room_terms(evidence_text) and not has_warehouse_terms(evidence_text):
        reasons.append("warehouse_monitoring_field_cited_room_monitoring_source")

    if "并机" in answer_text and "单机" in evidence_text and "并机" not in evidence_text.replace("是否为并机", ""):
        reasons.append("answer_parallel_mode_conflicts_with_single_mode_evidence")
    if "单机" in answer_text and "并机" in evidence_text and "单机" not in evidence_text:
        reasons.append("answer_single_mode_conflicts_with_parallel_mode_evidence")

    return dedupe(reasons)


def combo_slot_mismatch_reasons(*, item: dict[str, Any], answer_value: Any, evidence_context: str) -> list[str]:
    field_text = normalize_support_text(
        " ".join(
            display_text(value)
            for value in [
                item.get("question_text"),
                item.get("instruction_text"),
                " ".join(display_text(part) for part in item.get("category_path") or []),
            ]
            if value
        )
    )
    if not is_chiller_combo_field(field_text):
        return []
    answer_text = normalize_support_text(display_text(answer_value))
    evidence_text = normalize_support_text(evidence_context)
    reasons: list[str] = []
    answer_has_pressure = has_chiller_pressure_slot(answer_text)
    answer_has_redundancy = has_chiller_redundancy_slot(answer_text)
    if not answer_has_pressure:
        reasons.append("missing_answer_chiller_pressure_slot")
    if not answer_has_redundancy:
        reasons.append("missing_answer_chiller_redundancy_slot")
    if answer_has_pressure and not has_chiller_pressure_slot(evidence_text):
        reasons.append("missing_evidence_chiller_pressure_slot")
    if answer_has_redundancy and not has_chiller_redundancy_slot(evidence_text):
        reasons.append("missing_evidence_chiller_redundancy_slot")
    return dedupe(reasons)


def asks_oil_equipment(text: str) -> bool:
    return has_any_term(text, ["油机", "柴油", "柴发", "发电机", "柴油发电机"])


def asks_oil_parallel_control(text: str) -> bool:
    return has_oil_parallel_control_terms(text) or (asks_oil_equipment(text) and "并机" in text and "控制" in text)


def has_oil_equipment_terms(text: str) -> bool:
    return has_any_term(text, ["油机", "柴油", "柴发", "发电机", "发电机组", "柴油发电机"])


def has_oil_parallel_control_terms(text: str) -> bool:
    return has_any_term(text, ["油机并机控制", "柴油发电机并机控制", "柴发并机控制", "并机控制器", "并机控制"])


def has_oil_route_control_terms(text: str) -> bool:
    return has_any_term(text, ["油路控制", "油路系统", "油路控制系统"])


def has_non_oil_power_system_terms(text: str) -> bool:
    return has_any_term(text, ["ups", "不间断电源", "hvdc", "高压直流"])


def asks_warehouse_cctv(text: str) -> bool:
    return has_warehouse_terms(text) and has_monitoring_terms(text)


def has_warehouse_terms(text: str) -> bool:
    return has_any_term(text, ["库房", "仓库"])


def has_monitoring_terms(text: str) -> bool:
    return has_any_term(text, ["监控", "cctv", "摄像头", "视频"])


def has_room_terms(text: str) -> bool:
    return has_any_term(text, ["机房", "房间"])


def is_chiller_combo_field(text: str) -> bool:
    return has_any_term(text, ["冰机", "冷机", "冷水机组", "冷冻机组", "制冷主机"]) and (
        has_any_term(text, ["配置", "冗余", "高压", "低压"]) or "高压or低压" in text
    )


def has_chiller_pressure_slot(text: str) -> bool:
    return has_any_term(text, ["高压", "低压"])


def has_chiller_redundancy_slot(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", display_text(text)).lower()
    compact = normalize_support_text(normalized)
    return bool(
        re.search(r"(?:^|[^a-z])n\s*\+\s*1", normalized)
        or re.search(r"\d+\s*\+\s*1", normalized)
        or "n+1" in compact
        or "2n" in compact
        or "主备" in compact
    )


def has_any_term(text: str, terms: list[str]) -> bool:
    return any(normalize_support_text(term) in text for term in terms)


def parse_field_intent(*, item: dict[str, Any], answer_value: Any, room_context: str | None) -> FieldIntent:
    del room_context
    text = normalize_support_text(
        " ".join(
            display_text(value)
            for value in [
                item.get("question_text"),
                item.get("instruction_text"),
                item.get("answer_key"),
                item.get("field_name"),
                " ".join(str(part) for part in item.get("category_path") or []),
            ]
            if value
        )
    )
    answer_text = normalize_support_text(display_text(answer_value))
    kind = "unknown"
    if any(term in text for term in BOOLEAN_FIELD_TERMS):
        kind = "boolean"
    elif numeric_answer_tokens(answer_text) or any(term in text for term in NUMERIC_FIELD_TERMS) or re.search(r"多少|几|数", text):
        kind = "numeric"
    elif any(term in text for term in ["类型", "模式", "等级", "状态"]):
        kind = "enum"
    elif text:
        kind = "text"
    entity_terms = detect_entity_terms(text)
    metric_terms = detect_metric_terms(text)
    status_terms = detect_status_terms(text)
    scope_terms = detect_scope_terms(text)
    unit_terms = dedupe([*detect_unit_terms(text), *extract_units(answer_text)])
    return FieldIntent(
        kind=kind,
        entity_terms=entity_terms,
        metric_terms=metric_terms,
        status_terms=status_terms,
        scope_terms=scope_terms,
        unit_terms=unit_terms,
        negation_sensitive=kind == "boolean" or any(term in text for term in ["是否", "有无", "能否"]),
    )


def detect_entity_terms(text: str) -> list[str]:
    groups = [
        ("ups", ["ups", "不间断电源"]),
        ("hvdc", ["hvdc", "高压直流"]),
        ("pdu", ["pdu"]),
        ("机柜", ["机柜", "机架"]),
        ("市电", ["市电", "供电", "电源"]),
        ("双路市电", ["双路市电", "两路市电"]),
        ("变电站", ["变电站"]),
        ("油机并机控制", ["油机并机控制", "柴油发电机并机控制", "柴发并机控制", "并机控制器", "并机控制"]),
        ("油路控制", ["油路控制", "油路系统", "油路控制系统"]),
        ("油机", ["油机", "柴油", "柴发", "发电机", "发电机组", "柴油发电机"]),
        ("冰机", ["冰机", "冷机", "冷水机组", "冷冻机组", "制冷主机"]),
        ("监控", ["监控", "cctv", "摄像头", "视频"]),
        ("库房", ["库房", "仓库"]),
        ("端口", ["端口", "端子"]),
        ("链路", ["链路", "a/b", "a路", "b路"]),
        ("地址", ["地址", "位置"]),
        ("液冷", ["液冷"]),
    ]
    terms: list[str] = []
    for canonical, aliases in groups:
        if any(normalize_support_text(alias) in text for alias in aliases):
            terms.append(canonical)
            terms.extend(alias for alias in aliases if normalize_support_text(alias) != normalize_support_text(canonical))
    return dedupe(terms)


def detect_metric_terms(text: str) -> list[str]:
    groups = [
        ("数量", ["数量", "台数", "个数", "几台", "路数"]),
        ("容量", ["容量", "kva", "kw"]),
        ("功率", ["功率", "kw", "mw"]),
        ("配置", ["配置", "规格", "情况"]),
        ("类型", ["类型", "型式", "高压", "低压"]),
        ("模式", ["模式", "并机", "单机"]),
        ("冗余", ["冗余", "n+1", "2n", "主备"]),
        ("控制", ["控制", "控制器"]),
        ("电源", ["电源", "u电"]),
        ("支持", ["支持", "满足", "具备", "可用"]),
        ("双路", ["双路", "两路", "2路", "a/b"]),
        ("进线", ["进线", "来源"]),
        ("地址", ["地址", "位置"]),
        ("已建设", ["已建设", "已建", "现网", "当前"]),
        ("规划", ["规划", "计划", "未来", "拟建"]),
    ]
    terms: list[str] = []
    for canonical, aliases in groups:
        if any(normalize_support_text(alias) in text for alias in aliases):
            terms.append(canonical)
    return dedupe(terms)


def detect_status_terms(text: str) -> list[str]:
    terms: list[str] = []
    if any(normalize_support_text(term) in text for term in CURRENT_TERMS):
        terms.append("current")
    if any(normalize_support_text(term) in text for term in PLANNED_TERMS):
        terms.append("planned")
    if any(normalize_support_text(term) in text for term in CONDITIONAL_TERMS):
        terms.append("conditional")
    return dedupe(terms)


def detect_scope_terms(text: str) -> list[str]:
    terms: list[str] = []
    if any(term in text for term in ["机房", "房间", "目标"]):
        terms.append("target")
    if any(term in text for term in ["园区", "全局", "基地"]):
        terms.append("global")
    return dedupe(terms)


def detect_unit_terms(text: str) -> list[str]:
    return extract_units(text)


def field_intent_summary(intent: FieldIntent) -> str:
    parts = [intent.kind]
    if intent.entity_terms:
        parts.append("entity=" + ",".join(intent.entity_terms))
    if intent.metric_terms:
        parts.append("metric=" + ",".join(intent.metric_terms))
    if intent.status_terms:
        parts.append("status=" + ",".join(intent.status_terms))
    if intent.unit_terms:
        parts.append("unit=" + ",".join(intent.unit_terms))
    return "; ".join(parts)


def evidence_field_path_parts(hits: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    for hit in hits:
        parent = hit.get("parent_payload")
        if isinstance(parent, dict):
            parts.extend(parent_payload_path_values(parent))
        for key in [
            "source_document",
            "file_name",
            "sheet_name",
            "table_id",
            "table_title",
            "section_path",
            "category",
            "capability_desc",
            "row_header",
            "column_header",
            "unit",
            "row_index",
            "cell_range",
            "source_type",
            "corpus_layer",
            "namespace",
        ]:
            value = hit.get(key)
            if isinstance(value, list):
                parts.extend(display_text(item) for item in value if display_text(item))
            elif display_text(value):
                parts.append(display_text(value))
        raw_prefix = structured_raw_prefix(hit.get("raw_text") or hit.get("text_for_embedding"))
        if raw_prefix:
            parts.append(raw_prefix)
    return dedupe(part for part in parts if part)


def parent_payload_path_parts(hits: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    for hit in hits:
        parent = hit.get("parent_payload")
        if isinstance(parent, dict):
            parts.extend(parent_payload_path_values(parent))
    return dedupe(part for part in parts if part)


def parent_payload_path_values(parent: dict[str, Any]) -> list[str]:
    values: list[str] = []
    known_keys = [
        "source_document",
        "sheet_name",
        "table_title",
        "section_path",
        "row_header",
        "column_header",
        "unit",
        "scope",
        "status",
        "parent_text",
        "neighbor_text",
        "row_index",
        "cell_range",
    ]
    for key in known_keys:
        value = parent.get(key)
        if isinstance(value, list):
            values.extend(display_text(item) for item in value if display_text(item))
        elif display_text(value):
            values.append(display_text(value))
    for key, value in parent.items():
        if key in {*known_keys, "confidence", "reasons"}:
            continue
        if isinstance(value, list):
            values.extend(display_text(item) for item in value if display_text(item))
        elif isinstance(value, dict):
            values.extend(flatten_payload_values(value))
        elif display_text(value):
            values.append(display_text(value))
    return values


def flatten_payload_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        parts: list[str] = []
        for key, child in value.items():
            if display_text(key):
                parts.append(display_text(key))
            parts.extend(flatten_payload_values(child))
        return parts
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(flatten_payload_values(item))
        return parts
    text = display_text(value)
    return [text] if text else []


def structured_raw_prefix(text: Any) -> str | None:
    raw = display_text(text)
    if not raw:
        return None
    prefix = re.split(r"[:：;；。\n]", raw, maxsplit=1)[0].strip()
    if 1 < len(prefix) <= 48:
        return prefix
    return None


def infer_expected_scope(*, item: dict[str, Any], room_context: str | None, target_namespace: str) -> str:
    del room_context
    text = normalize_support_text(
        " ".join(
            display_text(value)
            for value in [
                item.get("question_text"),
                item.get("instruction_text"),
                " ".join(str(part) for part in item.get("category_path") or []),
                target_namespace,
            ]
            if value
        )
    )
    if any(term in text for term in ["园区", "全局", "基地级"]):
        return "global"
    return "target"


def infer_evidence_scope(*, cited_hits: list[dict[str, Any]], target_namespace: str, text: str) -> str:
    namespaces = {str(hit.get("namespace") or "") for hit in cited_hits}
    normalized_text = normalize_support_text(text)
    if namespaces and namespaces.issubset({target_namespace}):
        return "global" if "园区" in normalized_text and "机房" not in normalized_text else "target"
    if "global" in namespaces or any(is_global_intro_hit(hit) for hit in cited_hits):
        return "global"
    if namespaces and target_namespace not in namespaces:
        return "other"
    return "unknown"


def infer_expected_status(intent: FieldIntent) -> str | None:
    if "planned" in intent.status_terms:
        return "planned"
    if "conditional" in intent.status_terms:
        return "conditional"
    if "current" in intent.status_terms:
        return "current"
    return "current" if intent.kind == "boolean" and intent.negation_sensitive else None


def infer_evidence_status(text: str) -> str | None:
    normalized = normalize_support_text(text)
    if any(normalize_support_text(term) in normalized for term in CONDITIONAL_TERMS):
        return "conditional"
    if any(normalize_support_text(term) in normalized for term in PLANNED_TERMS):
        return "planned"
    if any(normalize_support_text(term) in normalized for term in CURRENT_TERMS):
        return "current"
    return None


def status_conflicts(expected_status: str | None, evidence_status: str | None) -> bool:
    if not expected_status or not evidence_status:
        return False
    if expected_status == evidence_status:
        return False
    if expected_status == "current" and evidence_status in {"planned", "conditional"}:
        return True
    if expected_status == "planned" and evidence_status == "current":
        return True
    return False


def extract_units(text: Any) -> list[str]:
    normalized = unicodedata.normalize("NFKC", display_text(text)).lower()
    units = [normalize_unit(next((group for group in match.groups() if group), match.group(0))) for match in UNIT_RE.finditer(normalized)]
    return dedupe(unit for unit in units if unit)


def normalize_unit(unit: str) -> str:
    normalized = unicodedata.normalize("NFKC", display_text(unit)).lower()
    return UNIT_ALIASES.get(normalized, normalized)


def units_conflict(expected_units: list[str], evidence_units: list[str]) -> bool:
    if not expected_units or not evidence_units:
        return False
    comparable_expected = set(expected_units)
    comparable_evidence = set(evidence_units)
    if comparable_expected.intersection(comparable_evidence):
        return False
    power_units = {"kva", "kw", "mw", "w"}
    if comparable_expected.intersection(power_units) and comparable_evidence.intersection(power_units):
        return True
    return False


def matched_terms(terms: list[str], text: str) -> list[str]:
    normalized = normalize_support_text(text)
    return dedupe(term for term in terms if normalize_support_text(term) and normalize_support_text(term) in normalized)


def raw_hit_text_is_short(hits: list[dict[str, Any]]) -> bool:
    raw = " ".join(display_text(hit.get("raw_text") or hit.get("text_for_embedding")) for hit in hits)
    normalized = normalize_support_text(raw)
    return len(normalized) <= 12 or bool(re.fullmatch(r"\d+(?:\.\d+)?(?:kva|kw|mw|w|路|台|个|套|u)?", normalized, re.IGNORECASE))


def max_risk_level(left: str, right: str) -> str:
    ranks = {"low": 0, "medium": 1, "high": 2}
    return left if ranks.get(left, 0) >= ranks.get(right, 0) else right


def strength_rank(strength: str) -> int:
    return STRENGTH_ORDER.get(str(strength or "").upper(), 0)


def tokenize(text: Any) -> list[str]:
    normalized = unicodedata.normalize("NFKC", display_text(text)).lower()
    tokens: list[str] = []
    tokens.extend(match.group(0) for match in ASCII_TOKEN_RE.finditer(normalized))
    for match in CHINESE_RE.finditer(normalized):
        segment = match.group(0)
        if len(segment) == 1:
            tokens.append(segment)
            continue
        tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        if len(segment) > 2:
            tokens.extend(segment[index : index + 3] for index in range(len(segment) - 2))
    return dedupe(tokens)


def core_answer_tokens(answer_value: str) -> list[str]:
    text = display_text(answer_value).lower()
    if text in WEAK_ANSWER_VALUES:
        return []
    numeric = numeric_answer_tokens(text)
    lexical = [
        token
        for token in tokenize(text)
        if len(token) > 1
        and token not in {"情况", "是否", "来自", "同一", "一个", "支持", "满足"}
        and not re.fullmatch(r"[a-z]", token)
    ]
    return dedupe([*numeric, *lexical])


def numeric_answer_tokens(text: str) -> list[str]:
    return dedupe(re.sub(r"\s+", "", match.group(0).lower()) for match in NUMBER_UNIT_RE.finditer(text) if match.group(0).strip())


def normalize_support_text(text: str) -> str:
    return re.sub(r"\s+", "", display_text(text).lower())


def partition_tokens(tokens: list[str], evidence_text: str) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for token in tokens:
        normalized = normalize_support_text(token)
        if normalized and normalized in evidence_text:
            matched.append(token)
        else:
            missing.append(token)
    return matched, missing


def field_related(question_text: str, hits: list[dict[str, Any]]) -> bool:
    query_tokens = [token for token in tokenize(question_text) if len(token) > 1]
    if not query_tokens:
        return bool(hits)
    evidence_text = normalize_support_text(" ".join(display_text(hit.get("raw_text") or hit.get("text_for_embedding")) for hit in hits))
    matched = sum(1 for token in query_tokens if normalize_support_text(token) in evidence_text)
    return matched >= max(1, min(2, len(query_tokens)))


def is_global_intro_hit(hit: dict[str, Any]) -> bool:
    return hit.get("retrieval_layer") == "global_intro" or (
        str(hit.get("namespace") or "") == "global" and str(hit.get("source_type") or "").startswith("intro_doc")
    )


def is_exact_structured_hit(hit: dict[str, Any], target_namespace: str) -> bool:
    return str(hit.get("namespace") or "") == target_namespace and (
        hit.get("retrieval_layer") in {"target_main_fact", "target_structured_detail"}
        or hit.get("source_type") in {"main_excel_capability", "embedded_word_table"}
    )


def is_boolean_field(question_text: str) -> bool:
    return any(term in question_text for term in ["是否", "有无", "能否", "支持", "满足", "具备"])


def boolean_answer_supported(question_text: str, answer_value: str, evidence_text: str) -> bool:
    if not is_boolean_field(question_text):
        return True
    answer = display_text(answer_value)
    answer_has_yes = any(term in answer for term in YES_TERMS)
    answer_has_no = any(term in answer for term in NO_TERMS)
    if answer_has_no:
        return any(term in evidence_text for term in NO_TERMS)
    if answer_has_yes:
        return any(term in evidence_text for term in YES_TERMS)
    return True


def contains_field_key_token(tokens: list[str]) -> bool:
    normalized = {normalize_support_text(token) for token in tokens}
    return bool(normalized.intersection(FIELD_KEY_TERMS))


def dedupe(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output
