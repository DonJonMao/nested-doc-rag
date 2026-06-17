from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class EvidenceStrengthResult:
    evidence_strength: str
    reasons: list[str]
    valid_source_chunk_ids: list[str]
    invalid_source_chunk_ids: list[str]
    cited_hit_ids: list[str]
    matched_answer_tokens: list[str]
    missing_answer_tokens: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_strength": self.evidence_strength,
            "strength_reasons": self.reasons,
            "valid_source_chunk_ids": self.valid_source_chunk_ids,
            "invalid_source_chunk_ids": self.invalid_source_chunk_ids,
            "cited_hit_ids": self.cited_hit_ids,
            "matched_answer_tokens": self.matched_answer_tokens,
            "missing_answer_tokens": self.missing_answer_tokens,
        }


class EvidenceStrengthEvaluator:
    def __init__(
        self,
        *,
        target_namespace: str,
        global_intro_answer_allowed: bool = False,
        require_target_source_for_answered: bool = True,
    ) -> None:
        self.target_namespace = target_namespace
        self.global_intro_answer_allowed = global_intro_answer_allowed
        self.require_target_source_for_answered = require_target_source_for_answered

    def evaluate(self, *, item: dict[str, Any], prediction: Any, top_hits: list[dict[str, Any]]) -> EvidenceStrengthResult:
        status = str(getattr(prediction, "answer_status", "") or "")
        answer_value = display_text(getattr(prediction, "answer_value", ""))
        source_chunk_ids = [str(chunk_id) for chunk_id in getattr(prediction, "source_chunk_ids", []) or [] if chunk_id]
        hit_by_id = {str(hit.get("chunk_id")): hit for hit in top_hits if hit.get("chunk_id")}
        valid_source_chunk_ids = [chunk_id for chunk_id in source_chunk_ids if chunk_id in hit_by_id]
        invalid_source_chunk_ids = [chunk_id for chunk_id in source_chunk_ids if chunk_id not in hit_by_id]
        reasons: list[str] = []
        if status != "answered":
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
            )
        if not source_chunk_ids:
            return EvidenceStrengthResult("E0", ["no_source_chunk_ids"], [], [], [], [], [])
        if invalid_source_chunk_ids:
            reasons.append("cited_source_not_in_retrieved_hits")
        if not valid_source_chunk_ids:
            return EvidenceStrengthResult(
                "E0",
                dedupe([*reasons, "no_valid_evidence_support"]),
                [],
                invalid_source_chunk_ids,
                [],
                [],
                core_answer_tokens(answer_value),
            )

        cited_hits = [hit_by_id[chunk_id] for chunk_id in valid_source_chunk_ids]
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
        )


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

    if current_rank < required_answer_rank:
        review_required = True
        writeback_allowed = False
        risk_level = max_risk_level(risk_level, "medium")
        reasons.append("unsupported_by_strong_evidence")
        critic_flags.append("unsupported_by_strong_evidence")
        if downgrade_unsupported_answer_to_partial:
            suggested_status = "partial_clue"
            suggested_answer_value = "检索到相关线索，但证据强度不足以安全直接填写；请人工复核。"
    if current_rank < required_writeback_rank:
        writeback_allowed = False
        review_required = True
        risk_level = max_risk_level(risk_level, "medium")
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
