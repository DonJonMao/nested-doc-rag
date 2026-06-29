from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from nested_doc_rag.io import display_text
from nested_doc_rag.schemas.eval import FieldPrediction


@dataclass(frozen=True)
class SlotSpec:
    name: str
    label: str
    required: bool = True
    value_type: str = "short_text"
    canonical_hints: list[str] = field(default_factory=list)
    closed_set: bool = False
    allow_evidence_value: bool = True
    evidence_required: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SlotSpec:
        return cls(
            name=safe_name(value.get("name") or value.get("label") or "slot"),
            label=display_text(value.get("label") or value.get("name") or "slot"),
            required=bool(value.get("required", True)),
            value_type=display_text(value.get("value_type") or "short_text"),
            canonical_hints=[display_text(item) for item in value.get("canonical_hints") or value.get("allowed_values") or [] if display_text(item)],
            closed_set=bool(value.get("closed_set", False)),
            allow_evidence_value=bool(value.get("allow_evidence_value", True)),
            evidence_required=bool(value.get("evidence_required", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "required": self.required,
            "value_type": self.value_type,
            "canonical_hints": self.canonical_hints,
            "closed_set": self.closed_set,
            "allow_evidence_value": self.allow_evidence_value,
            "evidence_required": self.evidence_required,
        }


@dataclass(frozen=True)
class SlotDecomposition:
    is_composite: bool
    slots: list[SlotSpec] = field(default_factory=list)
    compose_rule: str = ""
    source: str = "heuristic"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, source: str = "agent") -> SlotDecomposition:
        slots = [SlotSpec.from_dict(item) for item in value.get("slots") or [] if isinstance(item, dict)]
        is_composite = bool(value.get("is_composite", bool(slots)))
        if is_composite and not slots:
            is_composite = False
        return cls(
            is_composite=is_composite,
            slots=slots,
            compose_rule=display_text(value.get("compose_rule") or ""),
            source=display_text(value.get("source") or source),
            confidence=safe_float(value.get("confidence"), default=0.0),
            reasons=[display_text(item) for item in value.get("reasons") or [] if display_text(item)],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_composite": self.is_composite,
            "slots": [slot.to_dict() for slot in self.slots],
            "compose_rule": self.compose_rule,
            "source": self.source,
            "confidence": self.confidence,
            "reasons": self.reasons,
        }

    def to_prompt_dict(self) -> dict[str, Any] | None:
        if not self.is_composite or not self.slots:
            return None
        return {
            "is_composite": True,
            "slots": [slot.to_dict() for slot in self.slots],
            "compose_rule": self.compose_rule,
            "rules": [
                "canonical_hints are soft normalization hints, never a hard answer whitelist.",
                "Prefer original evidence text when it is more specific than the hints.",
                "Return the original evidence-backed raw_value for each slot.",
                "Every required evidence_required slot must cite supporting source_chunk_ids.",
            ],
        }


@dataclass(frozen=True)
class SlotValue:
    name: str
    raw_value: str
    normalized_value: str = ""
    source_chunk_ids: list[str] = field(default_factory=list)
    evidence_attachment_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SlotValue:
        raw_value = display_text(value.get("raw_value") or value.get("value") or value.get("answer_value"))
        normalized_value = display_text(value.get("normalized_value") or value.get("canonical_value") or raw_value)
        return cls(
            name=safe_name(value.get("name") or value.get("slot_name") or ""),
            raw_value=raw_value,
            normalized_value=normalized_value,
            source_chunk_ids=[str(item) for item in value.get("source_chunk_ids") or [] if item],
            evidence_attachment_ids=[str(item) for item in value.get("evidence_attachment_ids") or [] if item],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "source_chunk_ids": self.source_chunk_ids,
            "evidence_attachment_ids": self.evidence_attachment_ids,
        }


@dataclass(frozen=True)
class SlotConsistencyResult:
    checked: bool
    passed: bool
    flags: list[str]
    reasons: list[str]
    decomposition: SlotDecomposition
    slot_values: list[SlotValue]
    missing_required_slots: list[str] = field(default_factory=list)
    unsupported_slots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "passed": self.passed,
            "flags": self.flags,
            "reasons": self.reasons,
            "decomposition": self.decomposition.to_dict(),
            "slot_values": [slot.to_dict() for slot in self.slot_values],
            "missing_required_slots": self.missing_required_slots,
            "unsupported_slots": self.unsupported_slots,
        }


EMPTY_DECOMPOSITION = SlotDecomposition(is_composite=False, slots=[], source="none", reasons=["not_composite"])


def build_slot_decomposition_messages(item: dict[str, Any]) -> list[dict[str, str]]:
    item_view = {
        "form_item_id": item.get("form_item_id"),
        "row_index": item.get("row_index"),
        "target_cell": item.get("target_cell"),
        "category_path": item.get("category_path") or [],
        "question_text": item.get("question_text"),
        "instruction_text": item.get("instruction_text"),
        "answer_example_format_only": item.get("answer_example"),
    }
    schema = {
        "is_composite": "true if the field requires multiple independent factual slots before one answer can be written",
        "slots": [
            {
                "name": "stable_ascii_slot_name",
                "label": "Chinese slot label",
                "required": True,
                "value_type": "short_text | number | boolean | date",
                "canonical_hints": ["normalization hints only; not hard answer whitelist"],
                "closed_set": False,
                "allow_evidence_value": True,
                "evidence_required": True,
            }
        ],
        "compose_rule": "short rule for composing slot values into one answer string",
        "confidence": "0-1",
        "reasons": ["short reasons"],
    }
    prompt = (
        "你是工勘字段拆槽 Agent。只根据表单字段本身判断这个字段是否需要多个事实槽才能安全填写。"
        "不要读取 heldout/gold answer，不要限制召回。\n"
        "如果字段是单一事实，输出 is_composite=false 且 slots=[]。"
        "如果字段是组合字段，拆出必须分别找证据的槽。canonical_hints 只是归一化提示，不是硬白名单；"
        "不要因为证据原文不在 hints 中就排除它。除非题面明确是封闭选项，否则 closed_set=false；"
        "即使题面是封闭选项，也要保留 evidence 里的原始 raw_value 供后续判断。\n\n"
        f"form_item:\n{json.dumps(item_view, ensure_ascii=False, indent=2)}\n\n"
        "请只输出严格 JSON，schema 如下：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )
    return [
        {"role": "system", "content": "Slot Decomposition Agent。你只做字段拆槽，输出严格 JSON。"},
        {"role": "user", "content": prompt},
    ]


def heuristic_slot_decomposition(item: dict[str, Any]) -> SlotDecomposition:
    text = normalize_text(
        " ".join(
            display_text(value)
            for value in [
                item.get("question_text"),
                item.get("instruction_text"),
                " ".join(display_text(part) for part in item.get("category_path") or []),
                item.get("answer_example"),
            ]
            if value
        )
    )
    slots: list[SlotSpec] = []
    compose_rule = ""
    reasons: list[str] = []

    if has_any(text, ["冰机", "冷机", "冷水机组", "冷冻机组", "制冷主机"]) and has_any(text, ["高压", "低压", "冗余", "n+1", "配置"]):
        slots = [
            SlotSpec(
                name="pressure_type",
                label="高压或低压",
                canonical_hints=["高压", "低压", "10kV", "低压供电"],
                closed_set=False,
            ),
            SlotSpec(
                name="redundancy",
                label="冗余配置",
                canonical_hints=["N+1", "2N", "主备", "一备一用"],
                closed_set=False,
            ),
        ]
        compose_rule = "pressure_type + '/' + redundancy"
        reasons.append("heuristic_chiller_combo")
    elif has_any(text, ["市电", "进线", "变电站"]) and has_any(text, ["路数", "几路", "双路", "两路", "来源", "同一变电站", "不同变电站"]):
        slots = [
            SlotSpec(
                name="utility_route_count",
                label="市电路数",
                value_type="short_text",
                canonical_hints=["1路", "2路", "双路", "两路"],
                closed_set=False,
            ),
            SlotSpec(
                name="utility_source_relation",
                label="市电来源关系",
                value_type="short_text",
                canonical_hints=["同一变电站", "不同变电站", "不同电源点"],
                closed_set=False,
            ),
        ]
        compose_rule = "utility_route_count + '，' + utility_source_relation"
        reasons.append("heuristic_utility_source_combo")
    elif has_any(text, ["油机", "柴油", "柴发", "发电机"]) and has_any(text, ["并机", "控制", "电源", "模式"]):
        slots = [
            SlotSpec(
                name="equipment_scope",
                label="设备对象",
                canonical_hints=["油机", "柴油发电机", "柴发", "发电机组"],
                closed_set=False,
            ),
            SlotSpec(
                name="mode_or_power",
                label="模式/控制/电源事实",
                canonical_hints=["并机", "单机", "控制器", "市电", "U电"],
                closed_set=False,
            ),
        ]
        compose_rule = "equipment_scope + ':' + mode_or_power"
        reasons.append("heuristic_oil_machine_combo")

    if not slots:
        return EMPTY_DECOMPOSITION
    return SlotDecomposition(is_composite=True, slots=slots, compose_rule=compose_rule, source="heuristic", confidence=0.65, reasons=reasons)


def evaluate_slot_consistency(
    *,
    item: dict[str, Any],
    prediction: FieldPrediction,
    generated: dict[str, Any],
    top_hits: list[dict[str, Any]],
    decomposition: SlotDecomposition,
) -> SlotConsistencyResult:
    del item
    if prediction.answer_status != "answered" or not decomposition.is_composite:
        return SlotConsistencyResult(
            checked=False,
            passed=True,
            flags=[],
            reasons=["slot_check_skipped"],
            decomposition=decomposition,
            slot_values=[],
        )

    slot_values = slot_values_from_generated(generated)
    if not slot_values:
        slot_values = infer_slot_values_from_answer(prediction.answer_value, prediction.source_chunk_ids, decomposition)

    values_by_name = {slot.name: slot for slot in slot_values if slot.name}
    hit_by_id = {str(hit.get("chunk_id")): hit for hit in top_hits if hit.get("chunk_id")}
    all_source_ids = [chunk_id for chunk_id in prediction.source_chunk_ids if chunk_id in hit_by_id]
    evidence_text_by_slot: dict[str, str] = {}
    missing_required: list[str] = []
    unsupported: list[str] = []
    reasons: list[str] = []

    for spec in decomposition.slots:
        slot_value = values_by_name.get(spec.name)
        raw_value = display_text(slot_value.raw_value if slot_value else "")
        if spec.required and not raw_value:
            missing_required.append(spec.name)
            reasons.append(f"slot_missing:{spec.name}")
            continue
        if not raw_value or not spec.evidence_required:
            continue
        source_ids = [chunk_id for chunk_id in (slot_value.source_chunk_ids if slot_value else []) if chunk_id in hit_by_id]
        if not source_ids:
            source_ids = all_source_ids
        evidence_text = normalize_text(" ".join(display_text(hit_by_id[chunk_id].get("raw_text") or hit_by_id[chunk_id].get("text_for_embedding")) for chunk_id in source_ids))
        evidence_text_by_slot[spec.name] = evidence_text
        if not source_ids or not slot_value_supported(spec, raw_value, slot_value.normalized_value if slot_value else "", evidence_text):
            unsupported.append(spec.name)
            reasons.append(f"slot_evidence_missing:{spec.name}")

    flags: list[str] = []
    if missing_required:
        flags.append("slot_mismatch")
    if unsupported:
        flags.append("answer_evidence_mismatch")
        flags.append("slot_mismatch")
    return SlotConsistencyResult(
        checked=True,
        passed=not missing_required and not unsupported,
        flags=dedupe(flags),
        reasons=dedupe(reasons),
        decomposition=decomposition,
        slot_values=slot_values,
        missing_required_slots=dedupe(missing_required),
        unsupported_slots=dedupe(unsupported),
    )


def slot_values_from_generated(generated: dict[str, Any]) -> list[SlotValue]:
    return [SlotValue.from_dict(item) for item in generated.get("slot_values") or [] if isinstance(item, dict)]


def infer_slot_values_from_answer(answer_value: Any, source_chunk_ids: list[str], decomposition: SlotDecomposition) -> list[SlotValue]:
    answer = display_text(answer_value)
    normalized = normalize_text(answer)
    values: list[SlotValue] = []
    for spec in decomposition.slots:
        raw = infer_raw_slot_value(spec, answer, normalized)
        values.append(
            SlotValue(
                name=spec.name,
                raw_value=raw,
                normalized_value=raw,
                source_chunk_ids=list(source_chunk_ids),
            )
        )
    return values


def infer_raw_slot_value(spec: SlotSpec, answer: str, normalized_answer: str) -> str:
    if spec.name == "pressure_type":
        if "高压" in normalized_answer or "10kv" in normalized_answer:
            return first_matching_substring(answer, ["10kV高压", "高压", "10kV"]) or "高压"
        if "低压" in normalized_answer:
            return first_matching_substring(answer, ["低压"]) or "低压"
    if spec.name == "redundancy":
        redundancy = first_matching_substring(answer, ["N+1", "n+1", "2N", "2n", "主备", "一备一用", "冗余"])
        return redundancy or ""
    if spec.name == "utility_route_count":
        route = first_matching_substring(answer, ["2路", "两路", "双路", "1路", "一路"])
        return route or ""
    if spec.name == "utility_source_relation":
        relation = first_matching_substring(answer, ["同一变电站", "同一个变电站", "不同变电站", "不同电源点"])
        return relation or ""
    if spec.name == "equipment_scope":
        return first_matching_substring(answer, ["柴油发电机", "发电机组", "油机", "柴发", "发电机"]) or ""
    if spec.name == "mode_or_power":
        return first_matching_substring(answer, ["并机控制", "并机", "单机", "一路市电", "一路U电", "市电", "U电"]) or ""
    for hint in spec.canonical_hints:
        if normalize_text(hint) in normalized_answer:
            return hint
    return answer if len(answer) <= 80 else ""


def slot_value_supported(spec: SlotSpec, raw_value: str, normalized_value: str, evidence_text: str) -> bool:
    candidates = [raw_value, normalized_value]
    normalized_candidates = [normalize_text(candidate) for candidate in candidates if normalize_text(candidate)]
    if any(candidate and candidate in evidence_text for candidate in normalized_candidates):
        return True
    if spec.name == "utility_route_count":
        return any(term in evidence_text for term in ["2路", "两路", "双路", "二路"]) if any(term in normalize_text(raw_value) for term in ["2", "两", "双"]) else False
    if spec.name == "utility_source_relation":
        raw = normalize_text(raw_value)
        if "同" in raw and "变电站" in raw:
            return "同" in evidence_text and "变电站" in evidence_text
        if "不同" in raw and "变电站" in raw:
            return "不同" in evidence_text and "变电站" in evidence_text
    if spec.name == "redundancy":
        raw = normalize_text(raw_value)
        if "n+1" in raw or "主备" in raw or "备" in raw:
            return any(term in evidence_text for term in ["n+1", "主备", "一备", "备用", "冗余"])
    return False


def normalize_slot_decomposition(value: dict[str, Any] | None, item: dict[str, Any]) -> SlotDecomposition:
    if not isinstance(value, dict):
        return heuristic_slot_decomposition(item)
    result = SlotDecomposition.from_dict(value, source="agent")
    if not result.is_composite:
        return result
    if any(not slot.label or not slot.name for slot in result.slots):
        fallback = heuristic_slot_decomposition(item)
        return fallback if fallback.is_composite else result
    return result


def slot_cache_key(item: dict[str, Any]) -> str:
    payload = {
        "question_text": item.get("question_text"),
        "instruction_text": item.get("instruction_text"),
        "category_path": item.get("category_path") or [],
        "answer_example": item.get("answer_example"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def first_matching_substring(text: str, candidates: list[str]) -> str:
    normalized_text = normalize_text(text)
    for candidate in candidates:
        if normalize_text(candidate) in normalized_text:
            return candidate
    return ""


def has_any(text: str, terms: list[str]) -> bool:
    return any(normalize_text(term) in text for term in terms)


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", display_text(value)).lower()
    text = re.sub(r"\s+", "", text)
    return text


def safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().lower())
    text = text.strip("_")
    return text or "slot"


def safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


def dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
