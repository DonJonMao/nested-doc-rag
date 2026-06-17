from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nested_doc_rag.config import load_app_config
from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.gongkan_eval import BASE_CLOUD_FILE
from nested_doc_rag.io import display_text, read_jsonl
from nested_doc_rag.retrieval import QdrantRetriever, layered_rerank_hits, rerank_hits

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
) -> Step15RetrievalResult:
    if retrieval_mode == "layered":
        reranked_hits, vector_hits = layered_rerank_hits(
            query_text,
            retriever=retriever,
            target_namespace=target_namespace,
            global_namespace=global_namespace,
            allowed_layers=allowed_layers,
            reranker=reranker,
            layered_plan=layered_plan,
        )
        return Step15RetrievalResult(reranked_hits=reranked_hits, vector_hits=vector_hits, retrieval_mode=retrieval_mode)
    if retrieval_mode == "flat":
        vector_hits = retriever.search(
            query_text,
            namespaces=[target_namespace, global_namespace],
            layers=allowed_layers,
            top_k=vector_top_k,
        )
        reranked_hits = rerank_hits(query_text, vector_hits, rerank_top_n, reranker)
        return Step15RetrievalResult(reranked_hits=reranked_hits, vector_hits=vector_hits, retrieval_mode=retrieval_mode)
    raise ValueError(f"unsupported retrieval_mode: {retrieval_mode}")


def build_qdrant_answer_messages(
    item: dict[str, Any],
    query_text: str,
    hits: list[dict[str, Any]],
    *,
    room_context: str | None = None,
    prompt_version: str = "step15_compat",
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
        "external_room_context": display_text(room_context),
    }
    schema = build_answer_schema(prompt_version)
    agent_v2_rules = ""
    if prompt_version == "agent_v2":
        agent_v2_rules = (
            "not_found strict rule: Use not_found only when the retrieved evidence pack contains no relevant information for the field. "
            "If any retrieved evidence is related but insufficient for direct filling, output partial_clue.\n"
            "partial source rule: For partial_clue, always include reference_source_documents with chunk_id, namespace, source_type, retrieval_layer, "
            "source_anchor, and a short evidence preview.\n"
        )
    elif prompt_version != "step15_compat":
        raise ValueError(f"unsupported prompt_version: {prompt_version}")
    user_prompt = (
        "下面是一个工勘单填报项、外部已知目标机房上下文和 RAG 检索结果。"
        "请只使用 external_room_context 与 retrieved_chunks 中的信息生成答案，不能使用常识，不能使用表格最后一列答案、heldout answer、expected_value 或 gold answer。\n"
        "external_room_context 是业务流程已知的目标机房定位信息，可以用于消歧和组成机房名称；"
        "retrieved_chunks 是事实证据来源。answer_example_format_only 只能作为格式参考，不能作为事实来源。"
        "图片附件只作为证据标记，不 OCR。\n"
        "输出口径：\n"
        "1. 如果 retrieved_chunks 中有可直接回答当前指标的证据，answer_status=answered，并填写 answer_value、source_chunk_ids 和 evidence_attachment_ids。\n"
        "2. 如果只命中相关信息，但粒度不够、缺少台数/实测值/房间粒度，或格式口径不足以直接填表，answer_status=partial_clue，answer_value 必须是“未找到”，"
        "只在 reference_source_documents 中列出参考来源文件、位置和原因，不把它当直接证据。\n"
        "3. 如果没有相关信息，answer_status=not_found，answer_value 必须是“未找到”。not_found 只应在没有相关证据时使用。\n"
        f"{agent_v2_rules}"
        "4. 如果多个 retrieved_chunks 都像可用证据但互相冲突，交给你做智能体仲裁：优先同 namespace 的 main_excel_capability，"
        "其次精确指标行，最后才是 global/intro_doc 长段说明；无法裁决时 answer_status=conflict_unresolved，answer_value 必须是“未找到”。\n"
        "5. 对 main_excel_capability 的 raw_text，斜杠前后的能力描述也是证据的一部分，不只看最后的现状/答案。"
        "例如 raw_text 包含“几路/两路进线/是否来自不同变电站”等指标描述，且现状/答案给出“来自同一个变电站”，"
        "可以组合成“2路市电，同一变电站”这类答案。"
        "如果同 namespace 精确指标行与 global/intro_doc 泛说明冲突，优先采用同 namespace 精确指标行，并在 agent_resolution.reason 说明低优先级来源被忽略。\n"
        "6. 如果 retrieved_chunks 带有 retrieval_layer/layer_priority，请按 layer_priority 从小到大审阅。"
        "target_main_fact 和 main_excel_capability raw_text 是强证据；上层不足时再看下层；下层可补充上层缺口，但不能无理由覆盖上层直接证据。"
        "global/intro 相关但不直接的内容应该保留为 reference_source_documents。\n"
        "字段规则：如果 question_text 是“机房名称”，answer_value 必须保留 external_room_context 中的具体房间/机房编号；"
        "如果 retrieved_chunks 给出正式数据中心或楼栋名称，可与该房间/机房编号组合成完整名称。"
        "如果 question_text 是“机房地址”，只返回物理地址，不追加房间号。\n"
        "如果 external_room_context 与 retrieved_chunks 合起来仍没有明确证据，answer_value 必须是“未找到”。\n\n"
        f"masked_query:\n{query_text}\n\n"
        f"form_item_without_heldout_answer:\n{json.dumps(item_view, ensure_ascii=False, indent=2)}\n\n"
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
        "file_name": "仅作参考线索的来源文件名",
        "anchor": "来源位置",
        "chunk_id": "来源 chunk id",
        "reason": "为什么只是参考线索而不是可填证据",
    }
    if prompt_version == "agent_v2":
        reference_doc_schema.update(
            {
                "namespace": "来源 namespace",
                "source_type": "来源 source_type",
                "retrieval_layer": "来源 retrieval_layer",
                "source_anchor": "来源锚点",
                "text_preview": "短证据预览",
            }
        )
    return {
        "answer_value": "可直接填入工勘单的短答案；没有足够直接证据时填“未找到”",
        "answer_status": "answered | partial_clue | not_found | conflict_unresolved",
        "confidence": "0-1",
        "source_chunk_ids": ["直接支撑 answer_value 的 chunk id；partial_clue/not_found 时为空数组"],
        "evidence_attachment_ids": ["直接支撑 answer_value 的附件 id；partial_clue/not_found 时为空数组"],
        "reference_source_documents": [reference_doc_schema],
        "agent_resolution": {
            "used": "是否进行了智能体仲裁/格式转换/冲突处理",
            "action": "none | select_source | format_transform | conflict_marked | clue_only",
            "reason": "简短说明",
        },
        "missing_fields": ["缺失字段"],
        "notes": "边界说明",
    }


def normalize_hit_for_prompt(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": hit.get("chunk_id"),
        "rank": hit.get("final_rank", hit.get("rerank_rank", hit.get("vector_rank"))),
        "score": hit.get("rerank_score", hit.get("vector_score")),
        "retrieval_layer": hit.get("retrieval_layer"),
        "layer_priority": hit.get("layer_priority"),
        "layer_description": hit.get("layer_description"),
        "namespace": hit.get("namespace"),
        "source_type": hit.get("source_type"),
        "corpus_layer": hit.get("corpus_layer"),
        "file_name": hit.get("file_name"),
        "anchor": hit.get("anchor") or hit.get("source_anchor"),
        "raw_text": hit.get("raw_text"),
        "text_for_embedding": hit.get("text_for_embedding"),
        "proof_attachment_ids": hit.get("proof_attachment_ids") or hit.get("evidence_attachment_ids") or [],
    }
