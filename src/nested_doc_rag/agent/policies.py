from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from nested_doc_rag.evaluation.field_metrics import normalize_bool, normalize_enum, normalize_text, validate_constraints
from nested_doc_rag.io import display_text
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction

from .state import EvidenceBundle, FieldState, QueryPlan, RepairDecision, ValidationResult

PREFERRED_SOURCE_TYPES = ["main_excel_capability", "embedded_word_table", "intro_doc_paragraph"]
UNCERTAIN_VALUES = {"可能", "待复核", "不确定", "未知", "待确认", "需确认"}
ANSWERED = "answered"
ABSTAIN_STATUSES = {"partial_clue", "not_found", "conflict_unresolved"}
MAX_REFERENCE_CHUNKS = 5


class EvidenceSupportLevel:
    DIRECT = "direct"
    REFERENCE = "reference"
    NONE = "none"
    CONFLICT = "conflict"


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
        intent=classify_field_intent(field),
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
    layers_seen = retrieval_layers(candidates, query_plan)
    if not candidates:
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[],
            reference_chunks=[],
            ignored_chunks=[],
            decision="no_evidence",
            reason="no candidate chunks matched the field",
            answer_status_hint="not_found",
            support_level=EvidenceSupportLevel.NONE,
            directness_reason="no candidates",
            retrieval_layers_seen=layers_seen,
        )

    direct_candidates: list[dict[str, Any]] = []
    reference_candidates: list[dict[str, Any]] = []
    ignored_chunks: list[dict[str, Any]] = []
    directness_reasons: list[str] = []
    for chunk in candidates:
        support_level, reason = classify_evidence_support(field, chunk, query_plan)
        annotated = {**chunk, "support_level": support_level, "directness_reason": reason}
        if support_level == EvidenceSupportLevel.DIRECT:
            direct_candidates.append(annotated)
        elif support_level == EvidenceSupportLevel.REFERENCE:
            reference_candidates.append(annotated)
        else:
            ignored_chunks.append(annotated)
        if reason:
            directness_reasons.append(f"{chunk.get('chunk_id')}: {reason}")

    direct_candidates = sorted(direct_candidates, key=lambda chunk: ranking_key(chunk, query_plan))
    reference_candidates = sorted(reference_candidates, key=lambda chunk: ranking_key(chunk, query_plan))
    ignored_chunks = sorted(ignored_chunks, key=lambda chunk: ranking_key(chunk, query_plan))

    conflict = same_priority_conflict(direct_candidates, query_plan)
    if conflict:
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[],
            reference_chunks=dedupe_chunks([*conflict, *reference_candidates])[:MAX_REFERENCE_CHUNKS],
            ignored_chunks=[chunk for chunk in candidates if chunk.get("chunk_id") not in set(chunk_ids(conflict))],
            decision="conflict_unresolved",
            reason="same-priority direct evidence has conflicting answer values",
            conflict_detected=True,
            answer_status_hint="conflict_unresolved",
            support_level=EvidenceSupportLevel.CONFLICT,
            directness_reason="same-priority direct answer_value conflict",
            retrieval_layers_seen=layers_seen,
        )

    selected = choose_target_direct_chunk(direct_candidates, field, query_plan)
    if selected is not None:
        suppressed = [chunk for chunk in direct_candidates if chunk.get("chunk_id") != selected.get("chunk_id")]
        references = dedupe_chunks(reference_candidates)[:MAX_REFERENCE_CHUNKS]
        ignored = dedupe_chunks([*suppressed, *ignored_chunks])
        reason = "selected highest-priority direct evidence"
        if is_uncertain_answer(next((chunk for chunk in candidates if chunk.get("source_type") == "main_excel_capability"), {})):
            reason = "selected explicit embedded evidence because main table value was uncertain"
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[selected],
            reference_chunks=references,
            ignored_chunks=ignored,
            decision="use_direct_evidence",
            reason=reason,
            answer_status_hint="answered",
            support_level=EvidenceSupportLevel.DIRECT,
            directness_reason=selected.get("directness_reason") or "; ".join(directness_reasons[:3]),
            retrieval_layers_seen=layers_seen,
        )

    if reference_candidates:
        return EvidenceBundle(
            field_id=field.field_id,
            selected_chunks=[],
            reference_chunks=dedupe_chunks(reference_candidates)[:MAX_REFERENCE_CHUNKS],
            ignored_chunks=ignored_chunks,
            decision="clue_only",
            reason="related evidence was found but it is not direct enough to fill the field automatically",
            answer_status_hint="partial_clue",
            support_level=EvidenceSupportLevel.REFERENCE,
            directness_reason="; ".join(directness_reasons[:5]),
            retrieval_layers_seen=layers_seen,
        )

    return EvidenceBundle(
        field_id=field.field_id,
        selected_chunks=[],
        reference_chunks=[],
        ignored_chunks=ignored_chunks,
        decision="no_evidence",
        reason="candidate chunks did not contain usable direct or reference evidence",
        answer_status_hint="not_found",
        support_level=EvidenceSupportLevel.NONE,
        directness_reason="; ".join(directness_reasons[:5]),
        retrieval_layers_seen=layers_seen,
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
        answer_value = selected.get("answer_value")
        validation = dict(validation_base)
        if not answer_value:
            answer_value = selected.get("short_answer") or selected.get("raw_text") or selected.get("text_for_embedding") or selected.get("text") or ""
            answer_value = str(answer_value or "").strip()
            if len(answer_value) > 120:
                answer_value = answer_value[:119] + "..."
            validation["deterministic_from_raw_text"] = True
        return FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value=answer_value,
            answer_status="answered",
            confidence=0.9,
            source_chunk_ids=chunk_source_ids(bundle.selected_chunks),
            evidence_attachment_ids=chunk_attachment_ids(bundle.selected_chunks),
            reference_chunk_ids=chunk_ids(bundle.reference_chunks),
            reference_source_documents=reference_source_documents(bundle.reference_chunks),
            reference_snippets=reference_snippets(bundle.reference_chunks),
            validation=validation,
            method_name=method_name,
        )
    if bundle.decision == "clue_only":
        return make_partial_clue_prediction(field, bundle, method_name="field_filling_agent_reference")
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
            reference_chunk_ids=chunk_ids(bundle.reference_chunks),
            reference_source_documents=reference_source_documents(bundle.reference_chunks),
            reference_snippets=reference_snippets(bundle.reference_chunks),
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


def make_partial_clue_prediction(
    field: FieldGold,
    bundle: EvidenceBundle,
    method_name: str = "field_filling_agent_reference",
) -> FieldPrediction:
    reference_ids = chunk_ids(bundle.reference_chunks)
    return FieldPrediction(
        field_id=field.field_id,
        row_index=field.row_index,
        target_cell=field.target_cell,
        answer_value="未找到可直接填写的证据；检索到以下相关线索，请人工复核。",
        answer_status="partial_clue",
        confidence=0.45 if reference_ids else 0.35,
        source_chunk_ids=[],
        evidence_attachment_ids=[],
        reference_chunk_ids=reference_ids,
        reference_source_documents=reference_source_documents(bundle.reference_chunks),
        reference_snippets=reference_snippets(bundle.reference_chunks),
        validation={
            "evidence_decision": "clue_only",
            "evidence_reason": bundle.reason,
            "selected_chunk_ids": [],
            "reference_chunk_ids": reference_ids,
            "needs_human_review": True,
            "support_level": bundle.support_level,
            "directness_reason": bundle.directness_reason,
        },
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
    return classify_field_intent_text(question_text)


def classify_field_intent(field: FieldGold) -> str:
    return classify_field_intent_text(field.question_text)


def classify_field_intent_text(question_text: str) -> str:
    text = question_text or ""
    upper = text.upper()
    if any(key in text for key in ["液冷", "冷板", "CDU", "冷却液"]):
        return "liquid_cooling"
    if "UPS" in upper or any(key in text for key in ["电池", "不间断电源"]):
        return "ups"
    if any(key in text for key in ["油机", "柴油", "发电", "市电", "供电", "双路", "AB路", "A路", "B路", "空开", "容量"]):
        return "power_capacity"
    if any(key in text for key in ["空调", "制冷", "冷量", "冷却"]):
        return "cooling"
    if any(key in text for key in ["机柜", "U位", "尺寸", "承重"]):
        return "cabinet"
    if any(key in text for key in ["网络", "带宽", "专线", "交换机", "路由"]):
        return "network"
    if "UPS" in text.upper():
        return "ups"
    if any(key in text for key in ["门禁", "出入", "进出", "权限"]):
        return "access_control"
    if any(key in text for key in ["拍照", "管理制度", "制度", "禁止", "审批", "流程"]):
        return "security_policy"
    if any(key in text for key in ["巡检", "检查", "验收"]):
        return "inspection_report"
    if any(key in text for key in ["维护", "检修", "保养", "归档"]):
        return "maintenance_record"
    if any(key in text for key in ["报告", "记录", "附件", "证明", "测试"]):
        return "attachment_report"
    if any(key in text for key in ["机房名称", "楼", "房间", "地址", "位置", "名称"]):
        return "room_identity"
    if any(key in text for key in ["日期", "时间"]):
        return "inspection_report"
    return "general"


def classify_evidence_support(
    field: FieldGold,
    chunk: dict[str, Any],
    query_plan: QueryPlan,
) -> tuple[str, str]:
    text = chunk_text(chunk)
    answer_text = normalize_text(chunk.get("answer_value"))
    layer = infer_retrieval_layer(chunk, query_plan)
    namespace = str(chunk.get("namespace") or "")
    source_type = str(chunk.get("source_type") or "")
    intent = query_plan.intent or classify_field_intent(field)
    is_target = namespace == query_plan.target_namespace
    is_global = namespace in set(query_plan.fallback_namespaces) or namespace != query_plan.target_namespace
    relevant = field_relevance(field, text, answer_text)
    explicit_negative = detect_negative_or_not_applicable(" ".join([answer_text, text]))

    if intent == "liquid_cooling" and not contains_liquid_cooling_term(" ".join([text, answer_text])):
        return EvidenceSupportLevel.NONE, "liquid cooling field but evidence has no liquid-cooling term"

    if is_uncertain_answer(chunk):
        return EvidenceSupportLevel.REFERENCE, "answer is uncertain and requires review"

    if chunk_has_answer_value(chunk) and usable_answer(chunk) and chunk.get("field_id") == field.field_id and is_target:
        return EvidenceSupportLevel.DIRECT, "field_id-matched target answer_value"

    if explicit_negative and is_negative_capable_field(field, intent):
        if is_target and layer in {"target_main_fact", "target_structured_detail"}:
            return EvidenceSupportLevel.DIRECT, f"direct target evidence explicitly says {explicit_negative}"
        if policy_intent_can_use_global(intent) and policy_text_is_direct(text, field):
            return EvidenceSupportLevel.DIRECT, f"policy evidence explicitly says {explicit_negative}"

    if is_target:
        if layer in {"target_main_fact", "target_structured_detail"}:
            if chunk_has_answer_value(chunk) and usable_answer(chunk):
                return EvidenceSupportLevel.DIRECT, "target structured answer_value"
            if relevant and source_type in {"main_excel_capability", "embedded_word_table"}:
                return EvidenceSupportLevel.DIRECT, "target structured text matches field semantics"
            if relevant:
                return EvidenceSupportLevel.REFERENCE, "target structured evidence is relevant but not explicit enough"
            return EvidenceSupportLevel.REFERENCE, "target structured evidence lacks clear field semantics"
        if layer == "target_raw_detail":
            if relevant and has_explicit_answer_signal(field, text):
                return EvidenceSupportLevel.DIRECT, "target raw detail contains explicit field answer"
            if relevant or source_type:
                return EvidenceSupportLevel.REFERENCE, "target raw detail is related but not direct"
            return EvidenceSupportLevel.NONE, "target raw detail is not relevant"
        if relevant:
            return EvidenceSupportLevel.REFERENCE, "target evidence is relevant but from low-priority layer"
        return EvidenceSupportLevel.NONE, "target evidence is not relevant"

    if is_global:
        if policy_intent_can_use_global(intent) and policy_text_is_direct(text, field):
            return EvidenceSupportLevel.DIRECT, "global policy/process evidence directly answers a policy field"
        if relevant or answer_text:
            return EvidenceSupportLevel.REFERENCE, "global evidence is a reference clue only"
        return EvidenceSupportLevel.NONE, "global evidence is not relevant"

    return EvidenceSupportLevel.NONE, "unsupported evidence namespace"


def detect_negative_or_not_applicable(text: str) -> str | None:
    normalized = normalize_enum(text)
    phrase_patterns = [
        "不涉及",
        "无法提供",
        "未配置",
        "未建设",
        "不支持",
        "不具备",
        "暂无",
        "没有",
    ]
    for pattern in phrase_patterns:
        if normalize_enum(pattern) in normalized:
            return pattern
    single_negative = re.search(r"(?:^|[:：,，;；、\s])(无|否)(?:$|[。；;，,\s])", str(text))
    if single_negative:
        return single_negative.group(1)
    if normalized in {"无", "否", "na", "n/a"}:
        return str(text).strip()
    if re.search(r"\bN/?A\b", str(text), re.IGNORECASE):
        return "N/A"
    return None


def infer_retrieval_layer(chunk: dict[str, Any], query_plan: QueryPlan) -> str:
    layer = str(chunk.get("retrieval_layer") or "")
    if layer:
        return layer
    namespace = str(chunk.get("namespace") or "")
    source_type = str(chunk.get("source_type") or "")
    corpus_layer = str(chunk.get("corpus_layer") or "")
    is_target = namespace == query_plan.target_namespace
    if is_target and source_type == "main_excel_capability":
        return "target_main_fact"
    if is_target and source_type in {"embedded_word_table", "structured_detail", "detail_table"}:
        return "target_structured_detail"
    if is_target and (source_type in {"intro_doc_paragraph", "embedded_raw_segment", "raw paragraph", "detail"} or corpus_layer == "raw_text"):
        return "target_raw_detail"
    if not is_target and source_type in {"intro_doc_paragraph", "intro_doc_table_row"}:
        return "global_intro"
    if not is_target:
        return "global_detail"
    return "target_raw_detail"


def field_relevance(field: FieldGold, text: str, answer_text: str = "") -> bool:
    haystack = normalize_enum(" ".join([text, answer_text]))
    if not haystack:
        return False
    question = field.question_text or ""
    if normalize_enum(question) and normalize_enum(question) in haystack:
        return True
    important_terms = field_terms(field)
    if not important_terms:
        return False
    matches = sum(1 for term in important_terms if normalize_enum(term) in haystack)
    return matches >= 1 if len(important_terms) <= 2 else matches >= 2


def field_terms(field: FieldGold) -> list[str]:
    question = field.question_text or ""
    terms: list[str] = []
    keyword_groups = [
        ["液冷", "冷板", "CDU", "液冷机柜"],
        ["UPS", "电池", "不间断电源"],
        ["油机", "柴油", "发电"],
        ["市电", "供电", "双路", "AB路", "空开"],
        ["门禁", "出入", "进出"],
        ["拍照", "禁止", "制度", "审批", "管理"],
        ["巡检", "检查", "报告", "记录", "归档", "测试"],
        ["机柜", "U位", "尺寸", "承重"],
        ["网络", "带宽", "专线", "交换机"],
        ["机房", "名称", "位置", "地址", "楼"],
    ]
    for group in keyword_groups:
        if any(term.upper() in question.upper() for term in group):
            terms.extend(group)
    tokens = [item for item in re.split(r"[\s,，;；:：/()（）？?、]+", question) if len(item) >= 2]
    terms.extend(tokens)
    return list(dict.fromkeys(term for term in terms if term))


def contains_liquid_cooling_term(text: str) -> bool:
    return any(term in text for term in ["液冷", "冷板", "CDU", "液冷机柜", "冷却液"])


def is_negative_capable_field(field: FieldGold, intent: str) -> bool:
    if normalize_enum(field.field_type) in {"bool", "enum"}:
        return True
    text = field.question_text or ""
    return intent in {
        "power_capacity",
        "ups",
        "cooling",
        "liquid_cooling",
        "cabinet",
        "network",
        "access_control",
        "security_policy",
        "inspection_report",
        "maintenance_record",
        "attachment_report",
        "general",
    } or any(key in text for key in ["是否", "有无", "配置", "提供", "支持", "涉及", "报告", "记录", "能力"])


def policy_intent_can_use_global(intent: str) -> bool:
    return intent in {"security_policy", "access_control", "inspection_report", "maintenance_record", "attachment_report"}


def policy_text_is_direct(text: str, field: FieldGold) -> bool:
    if not text:
        return False
    policy_terms = ["制度", "流程", "禁止", "审批", "管理", "记录", "报告", "归档", "测试", "巡检", "维护", "检修", "出入", "门禁", "拍照"]
    return any(term in text for term in policy_terms) and field_relevance(field, text)


def has_explicit_answer_signal(field: FieldGold, text: str) -> bool:
    if not text:
        return False
    if chunk_value_like_signal(text):
        return True
    if normalize_enum(field.field_type) == "bool" and normalize_bool(text) is not None:
        return True
    return bool(detect_negative_or_not_applicable(text))


def chunk_value_like_signal(text: str) -> bool:
    return bool(re.search(r"[:：]\s*[\w一-龥]+", text) or re.search(r"\d+(?:\.\d+)?\s*(?:kW|KW|千瓦|U|台|个|A|V|℃|平米|平方米)", text))


def chunk_text(chunk: dict[str, Any]) -> str:
    return str(chunk.get("raw_text") or chunk.get("text_for_embedding") or chunk.get("text") or chunk.get("content") or "")


def retrieval_layers(chunks: list[dict[str, Any]], query_plan: QueryPlan) -> list[str]:
    return list(dict.fromkeys(infer_retrieval_layer(chunk, query_plan) for chunk in chunks))


def ranking_key(chunk: dict[str, Any], query_plan: QueryPlan) -> tuple[int, int, float, float, int]:
    layer_priority = int(chunk.get("layer_priority") or chunk.get("layer_rank") or source_priority(chunk, query_plan))
    score = float(chunk.get("rerank_score") or chunk.get("layer_score") or chunk.get("vector_score") or chunk.get("score") or chunk.get("deterministic_score") or 0)
    retrieval_index = int(chunk.get("_retrieval_index") or 0)
    return (source_priority(chunk, query_plan), layer_priority, -score, -float(chunk.get("deterministic_score") or 0), retrieval_index)


def dedupe_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, chunk in enumerate(chunks):
        key = str(chunk.get("chunk_id") or f"chunk_index_{index}")
        if key in seen:
            continue
        seen.add(key)
        output.append(chunk)
    return output


def reference_source_documents(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for chunk in chunks:
        text = chunk_text(chunk)
        documents.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "namespace": chunk.get("namespace"),
                "source_type": chunk.get("source_type"),
                "corpus_layer": chunk.get("corpus_layer"),
                "retrieval_layer": chunk.get("retrieval_layer"),
                "source_anchor": chunk.get("source_anchor") or chunk.get("anchor") or chunk.get("source") or {},
                "file_name": chunk.get("file_name"),
                "relative_path": chunk.get("relative_path"),
                "proof_attachment_ids": chunk.get("proof_attachment_ids") or chunk.get("evidence_attachment_ids") or [],
                "proof_attachments": chunk.get("proof_attachments") or [],
                "text_preview": display_text(text, 180),
            }
        )
    return documents


def reference_snippets(chunks: list[dict[str, Any]]) -> list[str]:
    return [display_text(chunk_text(chunk), 180) for chunk in chunks if chunk_text(chunk)]


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
        values = {answer_key(chunk.get("answer_value")) for chunk in bucket if chunk_has_answer_value(chunk) and usable_answer(chunk)}
        if len(values) > 1:
            return bucket
    return []


def choose_target_direct_chunk(chunks: list[dict[str, Any]], field: FieldGold, query_plan: QueryPlan) -> dict[str, Any] | None:
    main_chunks = [chunk for chunk in chunks if chunk.get("source_type") == "main_excel_capability" and direct_evidence_candidate(chunk)]
    embedded_chunks = [chunk for chunk in chunks if chunk.get("source_type") == "embedded_word_table" and direct_evidence_candidate(chunk)]
    if normalize_enum(field.field_type) == "bool" and main_chunks and embedded_chunks and all(is_uncertain_answer(chunk) for chunk in main_chunks):
        explicit_embedded = [chunk for chunk in embedded_chunks if normalize_bool(chunk.get("answer_value")) is not None]
        if explicit_embedded:
            return explicit_embedded[0]
    for chunk in chunks:
        if direct_evidence_candidate(chunk) and not is_uncertain_answer(chunk):
            return chunk
    return None


def usable_answer(chunk: dict[str, Any]) -> bool:
    return chunk.get("answer_status", ANSWERED) == ANSWERED and normalize_text(chunk.get("answer_value")) not in {"", "未找到"}


def direct_evidence_candidate(chunk: dict[str, Any]) -> bool:
    if chunk_has_answer_value(chunk):
        return usable_answer(chunk)
    text = chunk.get("raw_text") or chunk.get("text_for_embedding") or chunk.get("text")
    return chunk.get("answer_status", ANSWERED) == ANSWERED and normalize_text(text) != ""


def chunk_has_answer_value(chunk: dict[str, Any]) -> bool:
    return "answer_value" in chunk and normalize_text(chunk.get("answer_value")) != ""


def is_uncertain_answer(chunk: dict[str, Any]) -> bool:
    if not chunk_has_answer_value(chunk):
        return False
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
        output.extend(str(item) for item in chunk.get("evidence_attachment_ids") or chunk.get("proof_attachment_ids") or [] if item)
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
