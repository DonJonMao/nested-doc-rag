from __future__ import annotations

import json
from typing import Any

from nested_doc_rag.io import display_text
from nested_doc_rag.schemas.eval import FieldGold

from .state import EvidenceBundle, QueryPlan


def build_field_answer_messages(
    field: FieldGold,
    evidence_bundle: EvidenceBundle,
    query_plan: QueryPlan,
) -> list[dict[str, str]]:
    schema = {
        "answer_value": "string",
        "answer_status": "answered | partial_clue | not_found | conflict_unresolved",
        "confidence": 0.0,
        "source_chunk_ids": ["chunk_id"],
        "reference_chunk_ids": ["chunk_id"],
        "evidence_attachment_ids": [],
        "reason": "简短说明",
    }
    field_view = {
        "field_id": field.field_id,
        "row_index": field.row_index,
        "target_cell": field.target_cell,
        "question_text": field.question_text,
        "field_type": field.field_type,
        "required": field.required,
        "must_have_evidence": field.must_have_evidence,
        "constraints": field.constraints.to_dict() if hasattr(field.constraints, "to_dict") else {},
    }
    query_view = {
        "primary_query": query_plan.primary_query,
        "target_namespace": query_plan.target_namespace,
        "intent": query_plan.intent,
    }
    selected_evidence_view = [format_evidence_chunk(chunk) for chunk in evidence_bundle.selected_chunks]
    reference_evidence_view = [format_evidence_chunk(chunk) for chunk in evidence_bundle.reference_chunks]
    user_prompt = (
        "请为单个工勘单字段生成可写入 Excel 的短答案。\n"
        "强制规则：\n"
        "1. answered 只能基于 selected_evidence，不得使用常识或编造。\n"
        "2. reference_evidence 只能作为上下文或 partial_clue 线索，不能作为 answered 的 source_chunk_ids。\n"
        "3. 不得使用 expected_value、gold answer、heldout answer 或样例作为事实来源。\n"
        "4. answered 必须提供 source_chunk_ids，且只能来自 selected_evidence。\n"
        "5. partial_clue 可以提供 reference_chunk_ids，且只能来自 reference_evidence。\n"
        "6. 如果 selected_evidence 不能直接支撑答案，返回 partial_clue 或 not_found。\n"
        "7. not found 不等于不涉及；只有 selected_evidence 明确写出不涉及/无/否/未配置/无法提供时，才能输出这些值。\n"
        "8. 如果证据冲突，返回 conflict_unresolved。\n"
        "9. 输出必须是 JSON，不要 markdown。\n\n"
        f"field:\n{json.dumps(field_view, ensure_ascii=False, indent=2)}\n\n"
        f"query_plan:\n{json.dumps(query_view, ensure_ascii=False, indent=2)}\n\n"
        f"selected_evidence:\n{json.dumps(selected_evidence_view, ensure_ascii=False, indent=2)}\n\n"
        f"reference_evidence:\n{json.dumps(reference_evidence_view, ensure_ascii=False, indent=2)}\n\n"
        f"output_schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )
    return [
        {"role": "system", "content": "你是严谨的工勘单字段填写助手。你只能基于提供的 selected evidence 回答。没有证据不要编造。"},
        {"role": "user", "content": user_prompt},
    ]


def format_evidence_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    text = chunk.get("raw_text") or chunk.get("text_for_embedding") or chunk.get("text") or ""
    return {
        "chunk_id": chunk.get("chunk_id"),
        "namespace": chunk.get("namespace"),
        "source_type": chunk.get("source_type"),
        "corpus_layer": chunk.get("corpus_layer"),
        "source": chunk.get("source") or {},
        "anchor": chunk.get("anchor"),
        "text": display_text(text, 800),
        "evidence_attachment_ids": chunk.get("evidence_attachment_ids") or chunk.get("proof_attachment_ids") or [],
    }
