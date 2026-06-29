from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nested_doc_rag.io import display_text
from nested_doc_rag.schemas.eval import FieldPrediction

PASS_LABELS = {"exact", "near", "parent_exact"}
BLOCK_LABELS = {
    "field_mismatch",
    "scope_mismatch",
    "status_mismatch",
    "unsupported",
    "answer_evidence_mismatch",
    "uncertain",
}


@dataclass(frozen=True)
class FieldBindingAgentResult:
    checked: bool
    passed: bool
    label: str
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    evidence_chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "passed": self.passed,
            "label": self.label,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "evidence_chunk_ids": self.evidence_chunk_ids,
        }


SKIPPED_BINDING_RESULT = FieldBindingAgentResult(
    checked=False,
    passed=True,
    label="skipped",
    reasons=["field_binding_agent_skipped"],
)


def build_field_binding_messages(
    *,
    item: dict[str, Any],
    prediction: FieldPrediction,
    hits: list[dict[str, Any]],
    room_context: str | None,
    rule_binding: str | None = None,
) -> list[dict[str, str]]:
    item_view = {
        "form_item_id": item.get("form_item_id"),
        "row_index": item.get("row_index"),
        "target_cell": item.get("target_cell"),
        "category_path": item.get("category_path") or [],
        "question_text": item.get("question_text"),
        "instruction_text": item.get("instruction_text"),
        "answer_example_format_only": item.get("answer_example"),
        "room_context": room_context,
    }
    answer_view = {
        "answer_value": prediction.answer_value,
        "answer_status": prediction.answer_status,
        "source_chunk_ids": prediction.source_chunk_ids,
        "evidence_attachment_ids": prediction.evidence_attachment_ids,
        "rule_field_binding": rule_binding,
    }
    evidence_view = [normalize_hit_for_binding(hit) for hit in hits[:5]]
    schema = {
        "passed": "true only if the cited evidence directly supports this exact form field answer",
        "label": "exact | near | parent_exact | field_mismatch | scope_mismatch | status_mismatch | unsupported | answer_evidence_mismatch | uncertain",
        "confidence": "0-1",
        "reasons": ["short reason strings"],
        "evidence_chunk_ids": ["chunk ids used for the decision"],
    }
    prompt = (
        "你是工勘写回前的字段绑定核验 Agent。只判断答案、表单字段、引用证据是否一致；"
        "不要读取 heldout/gold answer，也不要用 allowed_values/canonical_hints 限制召回。\n"
        "重点检查：证据的能力描述/字段路径是否就是目标字段，而不是相邻或相似字段。"
        "对于“是/否/有/无/满足”这类短答案，只有当证据字段本身与目标字段一致时才 passed=true。"
        "如果证据只是同类系统、相邻能力项、泛化说明或目标机房外的事实，应输出 field_mismatch、scope_mismatch 或 unsupported。"
        "如果不确定，输出 uncertain 且 passed=false；后续只会转人工复核，不会修改原答案。\n\n"
        f"form_item:\n{json.dumps(item_view, ensure_ascii=False, indent=2)}\n\n"
        f"generated_answer:\n{json.dumps(answer_view, ensure_ascii=False, indent=2)}\n\n"
        f"cited_evidence:\n{json.dumps(evidence_view, ensure_ascii=False, indent=2)}\n\n"
        "请只输出严格 JSON，schema 如下：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )
    return [
        {"role": "system", "content": "Field Binding Verification Agent。你只做写回前字段-证据一致性核验，输出严格 JSON。"},
        {"role": "user", "content": prompt},
    ]


def normalize_field_binding_agent_result(value: dict[str, Any] | None, *, evidence_chunk_ids: list[str]) -> FieldBindingAgentResult:
    if not isinstance(value, dict):
        return FieldBindingAgentResult(
            checked=True,
            passed=False,
            label="uncertain",
            confidence=0.0,
            reasons=["field_binding_agent_invalid_response"],
            evidence_chunk_ids=evidence_chunk_ids,
        )
    label = display_text(value.get("label") or "uncertain").lower()
    if label not in PASS_LABELS | BLOCK_LABELS:
        label = "uncertain"
    confidence = safe_float(value.get("confidence"), default=0.0)
    passed = bool(value.get("passed", label in PASS_LABELS)) and label in PASS_LABELS and confidence >= 0.5
    return FieldBindingAgentResult(
        checked=True,
        passed=passed,
        label=label,
        confidence=confidence,
        reasons=[display_text(item) for item in value.get("reasons") or [] if display_text(item)],
        evidence_chunk_ids=[str(item) for item in value.get("evidence_chunk_ids") or evidence_chunk_ids if item],
    )


def select_binding_hits(prediction: FieldPrediction, top_hits: list[dict[str, Any]], *, fallback_count: int = 3) -> list[dict[str, Any]]:
    hit_by_id = {str(hit.get("chunk_id")): hit for hit in top_hits if hit.get("chunk_id")}
    selected = [hit_by_id[chunk_id] for chunk_id in prediction.source_chunk_ids if chunk_id in hit_by_id]
    if selected:
        return selected
    return list(top_hits[:fallback_count])


def normalize_hit_for_binding(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": hit.get("chunk_id"),
        "namespace": hit.get("namespace"),
        "source_type": hit.get("source_type"),
        "retrieval_layer": hit.get("retrieval_layer"),
        "source_anchor": hit.get("source_anchor") or hit.get("anchor"),
        "file_name": hit.get("file_name"),
        "sheet_name": hit.get("sheet_name"),
        "cell": hit.get("cell"),
        "raw_text": display_text(hit.get("raw_text") or hit.get("text_for_embedding"), 900),
        "parent_payload": display_text(hit.get("parent_payload") or hit.get("parent_text"), 700),
    }


def safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default
