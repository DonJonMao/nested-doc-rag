from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nested_doc_rag.config import load_app_config
from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.evidence_record import EvidenceAddress, infer_evidence_kind
from nested_doc_rag.gongkan_eval import BASE_CLOUD_FILE
from nested_doc_rag.io import display_text, read_jsonl
from nested_doc_rag.retrieval import QdrantRetriever, layered_rerank_hits

DEFAULT_CONFIG = load_app_config()
STEP12_DIR = DEFAULT_CONFIG.paths.artifacts_dir / "12_gongkan_form_analysis"


@dataclass(frozen=True)
class Step15RetrievalResult:
    reranked_hits: list[dict[str, Any]]
    vector_hits: list[dict[str, Any]]
    retrieval_mode: str
    trace_records: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None


def add_room_context(query_text: str, room_context: str | None) -> str:
    context = display_text(room_context)
    if not context:
        return query_text
    return f"外部已知目标机房上下文：{context}。{query_text}"


def all_base_cloud_rows(step12_dir: Path = STEP12_DIR) -> list[int]:
    rows = [
        int(item["row_index"])
        for item in read_jsonl(step12_dir / "form_items.jsonl")
        if item.get("file_name") == BASE_CLOUD_FILE
    ]
    return sorted(rows)


def run_step15_retrieval(
    query_text: str,
    *,
    retriever: QdrantRetriever,
    reranker: RerankClient,
    target_namespace: str,
    global_namespace: str,
    allowed_layers: list[str],
    retrieval_mode: str,
    vector_top_k: int,
    rerank_top_n: int,
    layered_plan: list[dict[str, Any]],
    schema_first_enabled: bool = False,
    schema_queries: list[str] | None = None,
) -> Step15RetrievalResult:
    del vector_top_k, rerank_top_n
    if retrieval_mode != "layered":
        raise ValueError(f"unsupported retrieval_mode: {retrieval_mode}")
    calls_before = getattr(retriever, "qdrant_query_calls", None)
    schema_selections: list[dict[str, Any]] = []
    reranked_hits, vector_hits = layered_rerank_hits(
        query_text,
        retriever=retriever,
        target_namespace=target_namespace,
        global_namespace=global_namespace,
        allowed_layers=allowed_layers,
        reranker=reranker,
        layered_plan=layered_plan,
        schema_first_enabled=schema_first_enabled,
        schema_queries=schema_queries,
        schema_selections=schema_selections,
    )
    calls_after = getattr(retriever, "qdrant_query_calls", None)
    observed_calls = (
        calls_after - calls_before
        if type(calls_before) is int and type(calls_after) is int and 0 <= calls_before <= calls_after
        else None
    )
    return Step15RetrievalResult(
        reranked_hits=reranked_hits, vector_hits=vector_hits, retrieval_mode="layered",
        metadata={"qdrant_query_calls": observed_calls,
                  "schema_first": {"enabled": schema_first_enabled, "selections": schema_selections}},
    )


def build_qdrant_answer_messages(
    item: dict[str, Any],
    query_text: str,
    hits: list[dict[str, Any]],
    *,
    room_context: str | None = None,
    prompt_version: str = "step15_compat",
    slot_schema: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    evidence = [normalize_hit_for_prompt(hit) for hit in hits]
    item_view = {
        "form_item_id": item["form_item_id"],
        "file_name": item["file_name"],
        "sheet_name": item["sheet_name"],
        "target_cell": item["target_cell"],
        "row_index": item["row_index"],
        "category_path": item.get("category_path") or [],
        "question_text": item.get("question_text"),
        "instruction_text": item.get("instruction_text"),
        "answer_example_format_only": item.get("answer_example"),
        "needs_evidence": item.get("needs_evidence"),
    }
    del room_context
    schema = build_answer_schema(prompt_version)
    slot_rules = ""
    if slot_schema:
        slot_rules = (
            "组合字段拆槽规则：slot_schema 描述当前字段需要分别取证的事实槽。"
            "如果 slot_schema.is_composite=true，必须在 slot_values 中逐槽输出 raw_value、normalized_value、source_chunk_ids 和 evidence_attachment_ids。"
            "canonical_hints 只是归一化提示，不是硬白名单；不要因为证据原文不在 hints 中就排除它。"
            "raw_value 必须优先保留 retrieved_chunks 中的原始表达，normalized_value 才可使用 hints 做短标签归一化。"
            "如果任一 required/evidence_required 槽没有直接证据，不要把该槽脑补进 answer_value，应输出 partial_clue。\n"
        )
    agent_v2_rules = ""
    if prompt_version in {"agent_v2", "agentic_v1"}:
        agent_v2_rules = (
            "not_found strict rule: Use not_found only when the retrieved evidence pack contains no relevant information for the field. "
            "If any retrieved evidence is related but insufficient for direct filling, output partial_clue.\n"
            "partial source rule: For partial_clue, include reference_source_documents with chunk_id, quote and reason. "
            "Source addresses are resolved by the system from retrieval metadata.\n"
        )
    agentic_rules = ""
    if prompt_version == "agentic_v1":
        agentic_rules = (
            "evidence diagnosis rule: Always output evidence_diagnosis. Diagnose only from retrieved_chunks, not outside knowledge. "
            "Use sufficient only when the current evidence directly supports the answer. Use missing_info for partial_clue when more slot-level evidence is needed. "
            "Use not_found_recovery for not_found when an alias, layer expansion, or source-specific search may still recover evidence. "
            "Use wrong_answer_risk for answered outputs with weak grounding, invalid source alignment, entity/scope mismatch, or risky conflict. "
            "Use uncertainty_conflict for unresolved competing candidates, and include candidate_answers with supporting/refuting chunk ids.\n"
        )
    elif prompt_version not in {"step15_compat", "agent_v2"}:
        raise ValueError(f"unsupported prompt_version: {prompt_version}")
    user_prompt = (
        "下面是一个工勘单填报项和 RAG 检索结果。"
        "请只使用 retrieved_chunks 中的信息生成答案，不能使用常识，不能使用表格最后一列答案、heldout answer、expected_value 或 gold answer。\n"
        "retrieved_chunks 是唯一事实证据来源。answer_example_format_only 只能作为格式参考，不能作为事实来源。"
        "图片附件只作为证据标记，不 OCR。\n"
        "引用定位规则：对采用的 source_chunk_ids 和参考线索，在 reference_source_documents 中提供 chunk_id 与 quote。"
        "只能选择当前 retrieved_chunks 中已有的 evidence_id/chunk_id；不得生成新编号、来源文件名或地址。"
        "文件、单元格、表行和段落位置由系统从 retrieval metadata 解析。"
        "quote 必须逐字摘录对应 chunk 的 raw_source_text；没有 raw_source_text 时才使用 raw_text。"
        "保留原文空格、换行、标点，不改写、不拼接多处文字，不从 text_for_embedding 摘录，也不自动复制整个段落。"
        "没有可引用原文时 quote 留空。quote 仅用于原文定位；定位成功不代表答案正确或可以自动回写。\n"
        "输出口径：\n"
        "1. 如果 retrieved_chunks 中有可直接回答当前指标的证据，answer_status=answered，并填写 answer_value、source_chunk_ids 和 evidence_attachment_ids。\n"
        "2. 如果只命中相关信息，但粒度不够、缺少台数/实测值/房间粒度，或格式口径不足以直接填表，answer_status=partial_clue，answer_value 必须是“未找到”，"
        "只在 reference_source_documents 中列出已有 chunk_id、quote 和原因。\n"
        "3. 如果没有相关信息，answer_status=not_found，answer_value 必须是“未找到”。not_found 只应在没有相关证据时使用。\n"
        f"{agent_v2_rules}"
        f"{agentic_rules}"
        "4. 如果多个 retrieved_chunks 都像可用证据但互相冲突，交给你做智能体仲裁：优先同 namespace 且字段、范围匹配的 structured_field，"
        "其次精确指标行，最后才是 global/intro_doc 长段说明；无法裁决时 answer_status=conflict_unresolved，answer_value 必须是“未找到”。\n"
        "5. 对 main_excel_capability 的 raw_text，斜杠前后的能力描述也是证据的一部分，不只看最后的现状/答案。"
        "例如 raw_text 包含“几路/两路进线/是否来自不同变电站”等指标描述，且现状/答案给出“来自同一个变电站”，"
        "可以组合成“2路市电，同一变电站”这类答案。"
        "如果同 namespace 精确指标行与 global/intro_doc 泛说明冲突，优先采用同 namespace 精确指标行，并在 agent_resolution.reason 说明低优先级来源被忽略。\n"
        "6. 如果 retrieved_chunks 带有 retrieval_layer/layer_priority，请按 layer_priority 从小到大审阅。"
        "target_structured_fact 提供明确字段和值，仍须检查字段、范围、状态与原文支持，不能仅凭 evidence_kind 或层名确认答案。"
        "上层不足时再看下层；下层可补充上层缺口，但不能无理由覆盖上层直接证据。"
        "global/intro 相关但不直接的内容应该保留为 reference_source_documents。\n"
        f"{slot_rules}"
        "字段规则：如果 question_text 是“机房名称”，必须以 retrieved_chunks 中的机房名称证据为准。"
        "如果 question_text 是“机房地址”，只返回物理地址，不追加房间号。\n"
        "如果 retrieved_chunks 中没有明确证据，answer_value 必须是“未找到”。\n\n"
        f"masked_query:\n{query_text}\n\n"
        f"form_item_without_heldout_answer:\n{json.dumps(item_view, ensure_ascii=False, indent=2)}\n\n"
        f"slot_schema:\n{json.dumps(slot_schema or {'is_composite': False, 'slots': []}, ensure_ascii=False, indent=2)}\n\n"
        f"retrieved_chunks:\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
        "请只输出严格 JSON，schema 如下：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )
    return [
        {
            "role": "system",
            "content": (
                "Answer Arbitration Agent。你是受约束的工勘表单 RAG 答案仲裁器。"
                "只能使用已给目标上下文和完整 layered evidence pack，必须在 answered / partial_clue / not_found / conflict_unresolved 中选择一个并输出 JSON。"
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def build_answer_schema(prompt_version: str) -> dict[str, Any]:
    reference_doc_schema = {
        "chunk_id": "只能从 retrieved_chunks 中选择已有 evidence_id/chunk_id",
        "reason": "采用该来源的原因；线索不足时说明不能直接填写的原因",
        "quote": "对应 chunk 原文中的逐字短引用；优先 raw_source_text，其次 raw_text；没有则留空",
    }
    schema = {
        "answer_value": "可直接填入工勘单的短答案；没有足够直接证据时填“未找到”",
        "answer_status": "answered | partial_clue | not_found | conflict_unresolved",
        "confidence": "0-1",
        "source_chunk_ids": ["直接支撑 answer_value 的 chunk id；partial_clue/not_found 时为空数组"],
        "evidence_attachment_ids": ["直接支撑 answer_value 的附件 id；partial_clue/not_found 时为空数组"],
        "reference_source_documents": [reference_doc_schema],
        "slot_values": [
            {
                "name": "slot name from slot_schema",
                "raw_value": "evidence-backed original expression for this slot",
                "normalized_value": "optional normalized value; may equal raw_value",
                "source_chunk_ids": ["chunk ids directly supporting this slot"],
                "evidence_attachment_ids": ["attachment ids directly supporting this slot"],
            }
        ],
        "agent_resolution": {
            "used": "是否进行了智能体仲裁/格式转换/冲突处理",
            "action": "none | select_source | format_transform | conflict_marked | clue_only",
            "reason": "简短说明",
        },
        "missing_fields": ["缺失字段"],
        "notes": "边界说明",
    }
    if prompt_version == "agentic_v1":
        schema["evidence_diagnosis"] = {
            "state_kind": "sufficient | missing_info | wrong_answer_risk | not_found_recovery | uncertainty_conflict | unresolved",
            "failure_modes": [
                "slot_missing | granularity_gap | entity_mismatch | attribute_mismatch | scope_mismatch | format_gap | temporal_gap | source_conflict | candidate_conflict | evidence_absence | weak_grounding"
            ],
            "sufficiency": "sufficient | insufficient | contradictory | risky",
            "missing_information_need": "string|null",
            "candidate_answers": [
                {
                    "value": "string",
                    "supporting_chunk_ids": [],
                    "refuting_chunk_ids": [],
                    "scope": "string|null",
                    "reason": "string|null",
                }
            ],
            "risk_reason": "string|null",
            "recommended_next_actions": [
                {
                    "action_type": "slot_targeted_retrieval | alias_retrieval | layer_expansion | source_specific_retrieval | contrastive_retrieval | disambiguation_retrieval | stop | mark_unresolved",
                    "query_text": "string",
                    "target_slot": "string|null",
                    "target_layer": "string|null",
                    "source_type_preference": "string|null",
                    "purpose": "string|null",
                    "semantic_invariant": {},
                    "expected_gain_type": "string|null",
                }
            ],
        }
    return schema


def normalize_hit_for_prompt(hit: dict[str, Any]) -> dict[str, Any]:
    raw_source_text = hit.get("raw_source_text")
    has_raw_source = isinstance(raw_source_text, str) and bool(raw_source_text)
    try:
        kind = infer_evidence_kind(hit)
    except ValueError:
        kind = hit.get("evidence_kind")
    address = EvidenceAddress.from_payload(hit).to_dict()
    normalized = {
        "chunk_id": hit.get("chunk_id"),
        "evidence_id": hit.get("evidence_id") or hit.get("chunk_id"),
        "evidence_kind": kind,
        "kind": kind,
        "field_name": hit.get("field_name"),
        "field_value": hit.get("field_value"),
        "address": address,
        "location": address,
        "text": raw_source_text if has_raw_source else hit.get("raw_text"),
        "rank": hit.get("final_rank", hit.get("rerank_rank", hit.get("vector_rank"))),
        "score": hit.get("rerank_score", hit.get("vector_score")),
        "retrieval_layer": hit.get("retrieval_layer"),
        "layer_priority": hit.get("layer_priority"),
        "layer_description": hit.get("layer_description"),
        "retrieval_round": hit.get("retrieval_round"),
        "triggered_by": hit.get("triggered_by"),
        "namespace": hit.get("namespace"),
        "source_type": hit.get("source_type"),
        "corpus_layer": hit.get("corpus_layer"),
        "file_name": hit.get("file_name"),
        "anchor": hit.get("anchor") or hit.get("source_anchor"),
        "raw_text": hit.get("raw_text"),
        "raw_source_text": raw_source_text if has_raw_source else None,
        "quote_text_space": "raw_source_text" if has_raw_source else ("raw_text" if hit.get("raw_text") else "unavailable"),
        "index_version": hit.get("index_version") or "unknown",
        "text_for_embedding": hit.get("text_for_embedding"),
        "proof_attachment_ids": hit.get("proof_attachment_ids") or hit.get("evidence_attachment_ids") or [],
    }
    parent_payload = compact_parent_payload_for_prompt(hit.get("parent_payload"))
    if parent_payload:
        normalized["evidence_header"] = format_parent_payload_header(parent_payload)
        normalized["parent_payload"] = parent_payload
    return normalized


def compact_parent_payload_for_prompt(parent_payload: Any) -> dict[str, Any]:
    if not isinstance(parent_payload, dict):
        return {}
    keys = [
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
        "confidence",
    ]
    return {key: parent_payload.get(key) for key in keys if parent_payload.get(key) is not None and parent_payload.get(key) != "" and parent_payload.get(key) != []}


def format_parent_payload_header(parent_payload: dict[str, Any]) -> str:
    structure_path = " / ".join(
        display_text(parent_payload.get(key))
        for key in ["source_document", "sheet_name", "table_title", "section_path"]
        if display_text(parent_payload.get(key))
    )
    field_path = " / ".join(
        display_text(parent_payload.get(key))
        for key in ["row_header", "column_header"]
        if display_text(parent_payload.get(key))
    )
    parts = [
        f"结构路径：{structure_path}" if structure_path else "",
        f"字段路径：{field_path}" if field_path else "",
        f"单位：{display_text(parent_payload.get('unit'))}" if display_text(parent_payload.get("unit")) else "",
        f"范围：{display_text(parent_payload.get('scope'))}" if display_text(parent_payload.get("scope")) else "",
        f"状态：{display_text(parent_payload.get('status'))}" if display_text(parent_payload.get("status")) else "",
    ]
    return "；".join(part for part in parts if part)
