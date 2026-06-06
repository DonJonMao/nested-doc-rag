from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from nested_doc_rag.evaluation.field_metrics import normalize_bool, normalize_enum, normalize_text, validate_constraints
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction

from .state import EvidenceBundle, FieldState, QueryPlan, RepairDecision, ValidationResult

PREFERRED_SOURCE_TYPES = ["main_excel_capability", "embedded_word_table", "intro_doc_paragraph"]
UNCERTAIN_VALUES = {"可能", "待复核", "不确定", "未知", "待确认", "需确认"}
ANSWERED = "answered"
ABSTAIN_STATUSES = {"partial_clue", "not_found", "conflict_unresolved"}


def build_query_plan(
    field: FieldGold,
    *,
    target_namespace: str,
    room_context: str | None = None,
    config: Any | None = None,
) -> QueryPlan:
    aliases = aliases_for_question(field.question_text)
    context_parts = [target_namespace, room_context or "", field.question_text, *field.accepted_aliases, *aliases]
    primary_query = " ".join(part for part in context_parts if part)
    fallback_queries = [field.question_text, *aliases]
    fallback_queries = list(dict.fromkeys(item for item in fallback_queries if item))
    global_namespace = getattr(getattr(config, "retrieval", None), "global_namespace", "global") if config else "global"
    return QueryPlan(
        field_id=field.field_id,
        question_text=field.question_text,
        primary_query=primary_query,
        fallback_queries=fallback_queries,
        target_namespace=target_namespace,
        fallback_namespaces=[global_namespace],
        preferred_source_types=PREFERRED_SOURCE_TYPES.copy(),
        required_evidence=field.must_have_evidence,
        intent=intent_for_question(field.question_text),
        reason="deterministic keyword query plan",
    )


def retrieve_from_mini_corpus(query_plan: QueryPlan, corpus: list[dict[str, Any]], field: FieldGold) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, raw_chunk in enumerate(corpus):
        chunk = dict(raw_chunk)
        score = 0.0
        if chunk.get("field_id") == field.field_id:
            score += 0.5
        elif coarse_question_match(field.question_text, chunk):
            score += 0.2
        else:
            continue
        if chunk.get("namespace") == query_plan.target_namespace:
            score += 0.3
        if chunk.get("source_type") == "main_excel_capability":
            score += 0.2
        elif chunk.get("source_type") == "embedded_word_table":
            score += 0.15
        chunk["deterministic_score"] = round(score, 6)
        chunk["_retrieval_index"] = index
        candidates.append(chunk)
    candidates.sort(key=lambda item: (-float(item.get("deterministic_score") or 0), int(item.get("_retrieval_index") or 0)))
    return [without_private_keys(chunk) for chunk in candidates]


def select_evidence(candidates: list[dict[str, Any]], field: FieldGold, query_plan: QueryPlan) -> EvidenceBundle:
    if not candidates:
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[],
            reference_chunks=[],
            ignored_chunks=[],
            decision="no_evidence",
            reason="no candidate chunks matched the field",
            answer_status_hint="not_found",
        )

    target_chunks = [chunk for chunk in candidates if chunk.get("namespace") == query_plan.target_namespace]
    global_chunks = [chunk for chunk in candidates if chunk.get("namespace") != query_plan.target_namespace]
    if not target_chunks:
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[],
            reference_chunks=global_chunks,
            ignored_chunks=[],
            decision="clue_only",
            reason="only global/reference evidence was found; global evidence cannot be filled as direct answer",
            answer_status_hint="partial_clue",
        )

    target_chunks = sorted(target_chunks, key=lambda chunk: (source_priority(chunk, query_plan), -float(chunk.get("deterministic_score") or 0)))
    conflict = same_priority_conflict(target_chunks, query_plan)
    if conflict:
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[],
            reference_chunks=conflict,
            ignored_chunks=[chunk for chunk in candidates if chunk not in conflict],
            decision="conflict_unresolved",
            reason="same-priority target evidence has conflicting answer values",
            conflict_detected=True,
            answer_status_hint="conflict_unresolved",
        )

    selected = choose_target_direct_chunk(target_chunks, field, query_plan)
    if selected is None:
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[],
            reference_chunks=target_chunks + global_chunks,
            ignored_chunks=[],
            decision="no_evidence",
            reason="target candidates did not contain a usable direct answer",
            answer_status_hint="not_found",
        )

    ignored = [chunk for chunk in candidates if chunk is not selected]
    reason = "selected highest-priority target namespace evidence"
    if is_uncertain_answer(next((chunk for chunk in target_chunks if chunk.get("source_type") == "main_excel_capability"), {})):
        reason = "selected explicit embedded evidence because main table value was uncertain"
    return EvidenceBundle(
        field_id=field.field_id,
        selected_chunks=[selected],
        reference_chunks=[],
        ignored_chunks=ignored,
        decision="use_direct_evidence",
        reason=reason,
        answer_status_hint="answered",
    )


def should_generate_answer(evidence_bundle: EvidenceBundle) -> bool:
    return evidence_bundle.decision == "use_direct_evidence"


def make_prediction_from_evidence(
    field: FieldGold,
    bundle: EvidenceBundle,
    method_name: str = "field_filling_agent",
) -> FieldPrediction:
    validation_base: dict[str, Any] = {
        "evidence_decision": bundle.decision,
        "evidence_reason": bundle.reason,
    }
    if bundle.decision == "use_direct_evidence":
        selected = bundle.selected_chunks[0]
        return FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value=selected.get("answer_value"),
            answer_status="answered",
            confidence=0.9,
            source_chunk_ids=chunk_source_ids(bundle.selected_chunks),
            evidence_attachment_ids=chunk_attachment_ids(bundle.selected_chunks),
            validation=validation_base,
            method_name=method_name,
        )
    if bundle.decision == "clue_only":
        return FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value="未找到",
            answer_status="partial_clue",
            confidence=0.4,
            source_chunk_ids=[],
            evidence_attachment_ids=[],
            validation={**validation_base, "reference_chunk_ids": chunk_ids(bundle.reference_chunks)},
            method_name=method_name,
        )
    if bundle.decision == "conflict_unresolved":
        return FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value="未找到",
            answer_status="conflict_unresolved",
            confidence=0.2,
            source_chunk_ids=[],
            evidence_attachment_ids=[],
            validation={**validation_base, "conflict_chunk_ids": chunk_ids(bundle.reference_chunks)},
            method_name=method_name,
        )
    return FieldPrediction(
        field_id=field.field_id,
        row_index=field.row_index,
        target_cell=field.target_cell,
        answer_value="未找到",
        answer_status="not_found",
        confidence=0.0,
        source_chunk_ids=[],
        evidence_attachment_ids=[],
        validation=validation_base,
        method_name=method_name,
    )


def validate_prediction_light(field: FieldGold, pred: FieldPrediction, config: Any | None = None) -> ValidationResult:
    threshold = getattr(getattr(config, "agent", None), "confidence_threshold", 0.65) if config else 0.65
    violations = normalize_constraint_violations(validate_constraints(field, pred))
    if field.must_have_evidence and pred.answer_status == ANSWERED and not pred.source_chunk_ids:
        violations.append("missing_evidence")
    if pred.answer_status == ANSWERED and normalize_text(pred.answer_value) == "":
        violations.append("required_missing")
    if pred.answer_status == ANSWERED and len(str(pred.answer_value or "")) > 120:
        violations.append("answer_too_long")
    if pred.confidence < float(threshold):
        violations.append("low_confidence")
    violations = sorted(set(violations))
    needs_human_review = pred.answer_status in ABSTAIN_STATUSES or bool(violations)
    return ValidationResult(
        passed=not violations,
        violations=violations,
        needs_human_review=needs_human_review,
        confidence=pred.confidence,
    )


def should_repair(validation: ValidationResult, state: FieldState, *, max_attempts: int = 1) -> RepairDecision:
    if len(state.repair_attempts) >= max_attempts:
        return RepairDecision(False, "none", "max repair attempts reached")
    if not state.draft_prediction or state.draft_prediction.answer_status != ANSWERED:
        return RepairDecision(False, "no_repair_human_review", "non-answered fields require human review")
    if state.evidence_bundle and state.evidence_bundle.conflict_detected:
        return RepairDecision(False, "no_repair_human_review", "conflict evidence cannot be repaired deterministically")
    if "missing_evidence" in validation.violations:
        return RepairDecision(False, "no_repair_human_review", "missing evidence cannot be repaired")
    if "enum_error" in validation.violations:
        return RepairDecision(True, "enum_error", "enum value may be repairable by alias or canonicalization")
    if any(item in validation.violations for item in {"bool_format_error", "number_format_error", "date_format_error"}):
        return RepairDecision(True, "format_error", "field value has a deterministic format repair")
    if "answer_too_long" in validation.violations:
        return RepairDecision(True, "answer_too_long", "long answer can be shortened deterministically")
    if "low_confidence" in validation.violations and state.evidence_bundle and state.evidence_bundle.reference_chunks:
        return RepairDecision(True, "low_confidence_fallback", "fallback evidence is available")
    return RepairDecision(False, "none", "no deterministic repair policy matched")


def should_human_review(state: FieldState) -> bool:
    prediction = state.final_prediction or state.draft_prediction
    if not prediction:
        return True
    if prediction.answer_status in ABSTAIN_STATUSES:
        return True
    if state.validation_result and state.validation_result.needs_human_review:
        return True
    if state.validation_result and not state.validation_result.passed and not state.repair_attempts:
        return True
    if state.evidence_bundle and state.evidence_bundle.conflict_detected:
        return True
    return state.must_have_evidence and prediction.answer_status == ANSWERED and not prediction.source_chunk_ids


def with_validation(prediction: FieldPrediction, validation: ValidationResult) -> FieldPrediction:
    return replace(
        prediction,
        validation={
            **prediction.validation,
            "validation_pass": validation.passed,
            "validation_violations": validation.violations,
            "needs_human_review": validation.needs_human_review,
        },
    )


def aliases_for_question(question_text: str) -> list[str]:
    text = question_text or ""
    aliases: list[str] = []
    if any(key in text for key in ["市电", "供电", "双路"]):
        aliases.extend(["双路市电", "两路供电", "供电能力"])
    if "UPS" in text.upper():
        aliases.extend(["不间断电源", "UPS容量", "后备电源"])
    if "门禁" in text:
        aliases.extend(["门禁系统", "出入控制", "门禁配置"])
    if "机房名称" in text:
        aliases.extend(["机房", "房间名称", "机房编号"])
    if any(key in text for key in ["日期", "巡检"]):
        aliases.extend(["巡检时间", "检查日期", "最近巡检"])
    return aliases


def intent_for_question(question_text: str) -> str:
    text = question_text or ""
    if any(key in text for key in ["市电", "供电", "双路"]):
        return "power_supply"
    if "UPS" in text.upper():
        return "ups"
    if "门禁" in text:
        return "access_control"
    if "机房名称" in text:
        return "room_identity"
    if any(key in text for key in ["日期", "巡检"]):
        return "inspection"
    return "general"


def coarse_question_match(question_text: str, chunk: dict[str, Any]) -> bool:
    haystack = " ".join(str(chunk.get(key) or "") for key in ("question_text", "text", "answer_value"))
    if question_text and question_text in haystack:
        return True
    tokens = [item for item in re.split(r"[\s,，;；:：/()（）]+", question_text or "") if len(item) >= 2]
    return any(token in haystack for token in tokens)


def source_priority(chunk: dict[str, Any], query_plan: QueryPlan) -> int:
    namespace = chunk.get("namespace")
    source_type = chunk.get("source_type")
    if namespace == query_plan.target_namespace and source_type == "main_excel_capability":
        return 1
    if namespace == query_plan.target_namespace and source_type == "embedded_word_table":
        return 2
    if namespace == query_plan.target_namespace and source_type != "intro_doc_paragraph":
        return 3
    if namespace == query_plan.target_namespace and source_type == "intro_doc_paragraph":
        return 4
    if namespace != query_plan.target_namespace and source_type == "intro_doc_paragraph":
        return 5
    return 6


def same_priority_conflict(chunks: list[dict[str, Any]], query_plan: QueryPlan) -> list[dict[str, Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for chunk in chunks:
        buckets.setdefault(source_priority(chunk, query_plan), []).append(chunk)
    for bucket in buckets.values():
        values = {answer_key(chunk.get("answer_value")) for chunk in bucket if usable_answer(chunk)}
        if len(values) > 1:
            return bucket
    return []


def choose_target_direct_chunk(chunks: list[dict[str, Any]], field: FieldGold, query_plan: QueryPlan) -> dict[str, Any] | None:
    main_chunks = [chunk for chunk in chunks if chunk.get("source_type") == "main_excel_capability" and usable_answer(chunk)]
    embedded_chunks = [chunk for chunk in chunks if chunk.get("source_type") == "embedded_word_table" and usable_answer(chunk)]
    if normalize_enum(field.field_type) == "bool" and main_chunks and embedded_chunks and all(is_uncertain_answer(chunk) for chunk in main_chunks):
        explicit_embedded = [chunk for chunk in embedded_chunks if normalize_bool(chunk.get("answer_value")) is not None]
        if explicit_embedded:
            return explicit_embedded[0]
    for chunk in chunks:
        if usable_answer(chunk) and not is_uncertain_answer(chunk):
            return chunk
    return None


def usable_answer(chunk: dict[str, Any]) -> bool:
    return chunk.get("answer_status", ANSWERED) == ANSWERED and normalize_text(chunk.get("answer_value")) not in {"", "未找到"}


def is_uncertain_answer(chunk: dict[str, Any]) -> bool:
    return normalize_enum(chunk.get("answer_value")) in {normalize_enum(item) for item in UNCERTAIN_VALUES}


def answer_key(value: Any) -> str:
    bool_value = normalize_bool(value)
    if bool_value is not None:
        return "是" if bool_value else "否"
    return normalize_enum(value)


def chunk_ids(chunks: list[dict[str, Any]]) -> list[str]:
    return [str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")]


def chunk_source_ids(chunks: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for chunk in chunks:
        source_ids = chunk.get("source_chunk_ids") or [chunk.get("chunk_id")]
        output.extend(str(item) for item in source_ids if item)
    return list(dict.fromkeys(output))


def chunk_attachment_ids(chunks: list[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    for chunk in chunks:
        output.extend(str(item) for item in chunk.get("evidence_attachment_ids") or [] if item)
    return list(dict.fromkeys(output))


def normalize_constraint_violations(violations: list[str]) -> list[str]:
    mapping = {
        "enum_not_allowed": "enum_error",
        "bool_invalid": "bool_format_error",
        "number_invalid": "number_format_error",
        "date_invalid": "date_format_error",
        "number_below_min": "number_range_error",
        "number_above_max": "number_range_error",
    }
    return [mapping.get(item, item) for item in violations]


def without_private_keys(chunk: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in chunk.items() if not key.startswith("_")}
