from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models


PROJECT_ROOT = Path("/Users/mao/projects/datacenter")
DEFAULT_OUT_DIR = PROJECT_ROOT / "artifacts/15_vector_store/base_cloud_closed_book_eval"
STEP15_DIR = PROJECT_ROOT / "artifacts/15_vector_store"
DEFAULT_COLLECTION = "datacenter_chunks_v1"
DEFAULT_TARGET_NAMESPACE = "xixian_4"
DEFAULT_EVAL_ROWS = [4, 5, 13, 16, 25, 26, 31, 36, 53, 117]
DEFAULT_QUERY_LAYERS = ["fact", "evidence", "intro_doc", "raw_text", "meta"]
DEFAULT_RETRIEVAL_MODE = "flat"


LAYERED_RETRIEVAL_PLAN = [
    {
        "layer_name": "target_main_fact",
        "description": "目标机房主知识库事实行，优先作为可填答案来源。",
        "namespaces": "target",
        "corpus_layers": ["fact", "evidence"],
        "source_types": ["main_excel_capability"],
        "vector_top_k": 16,
        "rerank_top_n": 5,
    },
    {
        "layer_name": "target_structured_detail",
        "description": "目标机房下钻出来的结构化表格内容，用于补充主表不足。",
        "namespaces": "target",
        "corpus_layers": ["fact"],
        "source_types": ["embedded_word_table"],
        "vector_top_k": 12,
        "rerank_top_n": 3,
    },
    {
        "layer_name": "target_raw_detail",
        "description": "目标机房下钻原文段落或表格行，只做补充线索。",
        "namespaces": "target",
        "corpus_layers": ["raw_text"],
        "source_types": ["embedded_raw_segment"],
        "vector_top_k": 12,
        "rerank_top_n": 3,
    },
    {
        "layer_name": "global_intro",
        "description": "全局介绍文档，用于解释园区级背景，不应无理由覆盖目标机房主表。",
        "namespaces": "global",
        "corpus_layers": ["intro_doc"],
        "source_types": ["intro_doc_paragraph", "intro_doc_table_row"],
        "vector_top_k": 10,
        "rerank_top_n": 3,
    },
    {
        "layer_name": "global_detail",
        "description": "全局下钻结构化或原文材料，只做低优先级补充。",
        "namespaces": "global",
        "corpus_layers": ["fact", "raw_text"],
        "source_types": ["embedded_word_table", "embedded_raw_segment"],
        "vector_top_k": 10,
        "rerank_top_n": 2,
    },
]

sys.path.insert(0, str(PROJECT_ROOT / "11_embedding_build"))
from embedding_pipeline import (  # noqa: E402
    DEFAULT_EMBEDDING_ENDPOINT,
    DEFAULT_EMBEDDING_MODEL,
    RerankClient,
    EmbeddingClient,
)

sys.path.insert(0, str(PROJECT_ROOT / "14_gongkan_rag_eval"))
from evaluate_base_cloud_form import (  # noqa: E402
    BASE_CLOUD_FILE,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_URL,
    STEP12_DIR,
    build_judge_messages,
    build_masked_query,
    call_deepseek_json,
    display_text,
    md,
    read_jsonl,
    select_eval_items,
    write_json,
    write_jsonl,
)


def add_room_context(query_text: str, room_context: str | None) -> str:
    context = display_text(room_context)
    if not context:
        return query_text
    return f"外部已知目标机房上下文：{context}。{query_text}"


def all_base_cloud_rows() -> list[int]:
    rows = [
        int(item["row_index"])
        for item in read_jsonl(STEP12_DIR / "form_items.jsonl")
        if item.get("file_name") == BASE_CLOUD_FILE
    ]
    return sorted(rows)


def build_qdrant_answer_messages(
    item: dict[str, Any],
    query_text: str,
    hits: list[dict[str, Any]],
    *,
    room_context: str | None = None,
) -> list[dict[str, str]]:
    evidence = [
        {
            "chunk_id": hit["chunk_id"],
            "rank": hit.get("rerank_rank", hit.get("vector_rank")),
            "score": hit.get("rerank_score", hit.get("vector_score")),
            "retrieval_layer": hit.get("retrieval_layer"),
            "layer_priority": hit.get("layer_priority"),
            "layer_description": hit.get("layer_description"),
            "namespace": hit["namespace"],
            "source_type": hit.get("source_type"),
            "corpus_layer": hit.get("corpus_layer"),
            "file_name": hit.get("file_name"),
            "anchor": hit.get("anchor"),
            "raw_text": hit.get("raw_text"),
            "proof_attachment_ids": hit.get("proof_attachment_ids") or [],
        }
        for hit in hits
    ]
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
    schema = {
        "answer_value": "可直接填入工勘单的短答案；没有足够直接证据时填“未找到”",
        "answer_status": "answered | partial_clue | not_found | conflict_unresolved",
        "confidence": "0-1",
        "source_chunk_ids": ["直接支撑 answer_value 的 chunk id；partial_clue/not_found 时为空数组"],
        "evidence_attachment_ids": ["直接支撑 answer_value 的附件 id；partial_clue/not_found 时为空数组"],
        "reference_source_documents": [
            {
                "file_name": "仅作参考线索的来源文件名",
                "anchor": "来源位置",
                "chunk_id": "来源 chunk id",
                "reason": "为什么只是参考线索而不是可填证据",
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
    user_prompt = (
        "下面是一个工勘单填报项、外部已知目标机房上下文和 RAG 检索结果。"
        "请只使用 external_room_context 与 retrieved_chunks 中的信息生成答案，不能使用常识，不能使用表格最后一列答案。\n"
        "external_room_context 是业务流程已知的目标机房定位信息，可以用于消歧和组成机房名称；"
        "retrieved_chunks 是事实证据来源。answer_example_format_only 只能作为格式参考，不能作为事实来源。"
        "图片附件只作为证据标记，不 OCR。\n"
        "输出口径：\n"
        "1. 如果 retrieved_chunks 中有可直接回答当前指标的证据，answer_status=answered，并填写 answer_value、source_chunk_ids 和 evidence_attachment_ids。\n"
        "2. 如果只命中相关信息，但粒度不够、缺少台数/实测值/房间粒度，或格式口径不足以直接填表，answer_status=partial_clue，answer_value 必须是“未找到”，"
        "只在 reference_source_documents 中列出参考来源文件、位置和原因，不把它当直接证据。\n"
        "3. 如果没有相关信息，answer_status=not_found，answer_value 必须是“未找到”。\n"
        "4. 如果多个 retrieved_chunks 都像可用证据但互相冲突，交给你做智能体仲裁：优先同 namespace 的 main_excel_capability，"
        "其次精确指标行，最后才是 global/intro_doc 长段说明；无法裁决时 answer_status=conflict_unresolved，answer_value 必须是“未找到”。\n"
        "5. 对 main_excel_capability 的 raw_text，斜杠前后的能力描述也是证据的一部分，不只看最后的现状/答案。"
        "例如 raw_text 包含“几路/两路进线/是否来自不同变电站”等指标描述，且现状/答案给出“来自同一个变电站”，"
        "可以组合成“2路市电，同一变电站”这类答案。"
        "如果同 namespace 精确指标行与 global/intro_doc 泛说明冲突，优先采用同 namespace 精确指标行，并在 agent_resolution.reason 说明低优先级来源被忽略。\n"
        "6. 如果 retrieved_chunks 带有 retrieval_layer/layer_priority，请按 layer_priority 从小到大审阅。"
        "上层不足时再看下层；下层可补充上层缺口，但不能无理由覆盖上层直接证据。\n"
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
        {"role": "system", "content": "你是受约束的 RAG 答案整理器，只能使用已给上下文和检索证据，必须输出 JSON。"},
        {"role": "user", "content": user_prompt},
    ]


class QdrantRetriever:
    def __init__(
        self,
        *,
        qdrant_path: Path,
        collection_name: str,
        embedding_endpoint: str,
        embedding_model: str,
    ) -> None:
        self.client = QdrantClient(path=str(qdrant_path))
        self.collection_name = collection_name
        self.embedder = EmbeddingClient(endpoint=embedding_endpoint, model=embedding_model)

    def close(self) -> None:
        self.client.close()

    def search(
        self,
        query: str,
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        vector = self.embedder.embed_query(query)
        return self.search_by_vector(
            vector,
            namespaces=namespaces,
            layers=layers,
            source_types=source_types,
            top_k=top_k,
        )

    def search_by_vector(
        self,
        vector: list[float],
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        conditions: list[models.FieldCondition] = [
            models.FieldCondition(key="namespace", match=models.MatchAny(any=namespaces)),
            models.FieldCondition(key="corpus_layer", match=models.MatchAny(any=layers)),
        ]
        if source_types:
            conditions.append(models.FieldCondition(key="source_type", match=models.MatchAny(any=source_types)))
        filters = models.Filter(
            must=conditions
        )
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=filters,
            limit=top_k,
            with_payload=True,
        )
        hits: list[dict[str, Any]] = []
        for rank, point in enumerate(response.points, 1):
            payload = point.payload or {}
            hits.append(
                {
                    "vector_rank": rank,
                    "vector_score": round(float(point.score), 6),
                    "chunk_id": payload.get("chunk_id"),
                    "namespace": payload.get("namespace"),
                    "source_type": payload.get("source_type"),
                    "corpus_layer": payload.get("corpus_layer"),
                    "anchor": payload.get("anchor"),
                    "file_name": payload.get("file_name"),
                    "raw_text": payload.get("raw_text"),
                    "text_for_embedding": payload.get("text_for_embedding") or payload.get("raw_text"),
                    "proof_attachment_ids": payload.get("proof_attachment_ids") or [],
                    "source": payload.get("source") or {},
                }
            )
        return hits


def rerank_hits(query_text: str, hits: list[dict[str, Any]], top_n: int, reranker: RerankClient) -> list[dict[str, Any]]:
    if not hits:
        return []
    docs = [hit.get("text_for_embedding") or hit.get("raw_text") or "" for hit in hits]
    reranked = reranker.rerank(query_text, docs, top_n=top_n)
    output: list[dict[str, Any]] = []
    for rank, rerank_item in enumerate(reranked, 1):
        index = int(rerank_item["index"])
        if index < 0 or index >= len(hits):
            continue
        hit = dict(hits[index])
        hit["rerank_rank"] = rank
        hit["rerank_score"] = rerank_item.get("relevance_score")
        output.append(hit)
    return output


def annotate_layer_hits(
    hits: list[dict[str, Any]],
    *,
    layer_name: str,
    layer_priority: int,
    layer_description: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for hit in hits:
        copied = dict(hit)
        copied["retrieval_layer"] = layer_name
        copied["layer_priority"] = layer_priority
        copied["layer_description"] = layer_description
        output.append(copied)
    return output


def layered_rerank_hits(
    query_text: str,
    *,
    retriever: QdrantRetriever,
    target_namespace: str,
    allowed_layers: list[str],
    reranker: RerankClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed_layer_set = set(allowed_layers)
    query_vector = retriever.embedder.embed_query(query_text)
    final_hits: list[dict[str, Any]] = []
    vector_hits: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()

    for layer_priority, spec in enumerate(LAYERED_RETRIEVAL_PLAN, 1):
        corpus_layers = [layer for layer in spec["corpus_layers"] if layer in allowed_layer_set]
        if not corpus_layers:
            continue
        namespaces = [target_namespace] if spec["namespaces"] == "target" else ["global"]
        layer_vector_hits = retriever.search_by_vector(
            query_vector,
            namespaces=namespaces,
            layers=corpus_layers,
            source_types=spec["source_types"],
            top_k=int(spec["vector_top_k"]),
        )
        layer_vector_hits = annotate_layer_hits(
            layer_vector_hits,
            layer_name=str(spec["layer_name"]),
            layer_priority=layer_priority,
            layer_description=str(spec["description"]),
        )
        vector_hits.extend(layer_vector_hits)
        layer_reranked = rerank_hits(query_text, layer_vector_hits, int(spec["rerank_top_n"]), reranker)
        for hit in layer_reranked:
            chunk_id = str(hit.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_chunk_ids:
                continue
            if chunk_id:
                seen_chunk_ids.add(chunk_id)
            final_hits.append(hit)

    for final_rank, hit in enumerate(final_hits, 1):
        hit["final_rank"] = final_rank
    return final_hits, vector_hits


def build_summary(
    results: list[dict[str, Any]],
    *,
    rows: list[int],
    collection_name: str,
    qdrant_path: Path,
    target_namespace: str,
    layers: list[str],
    room_context: str | None,
    retrieval_mode: str,
) -> dict[str, Any]:
    label_counts = Counter(result["judge"].get("label") for result in results)
    status_counts = Counter((result.get("generated_answer") or {}).get("answer_status") for result in results)
    numeric_scores = [float(result["judge"].get("score") or 0) for result in results]
    return {
        "retriever": "qdrant_full_store",
        "collection_name": collection_name,
        "qdrant_path": str(qdrant_path),
        "target_namespace": target_namespace,
        "namespace_filter": [target_namespace, "global"],
        "layer_filter": layers,
        "retrieval_mode": retrieval_mode,
        "layered_retrieval_plan": LAYERED_RETRIEVAL_PLAN if retrieval_mode == "layered" else [],
        "rows": rows,
        "completed_rows": [int(result["row_index"]) for result in results],
        "sample_count": len(results),
        "requested_sample_count": len(rows),
        "external_room_context": display_text(room_context),
        "answer_leakage_control": "heldout_answer/G列机房信息不进入 masked_query、Qdrant 检索、rerank 或 answer prompt，只在 judge 阶段使用；external_room_context 只表示业务流程已知的目标机房，不从 G 列读取。",
        "label_counts": dict(label_counts),
        "answer_status_counts": dict(status_counts),
        "average_score": round(sum(numeric_scores) / len(numeric_scores), 4) if numeric_scores else 0,
        "acceptable_or_better": sum(1 for result in results if result["judge"].get("label") in {"exact", "acceptable"}),
        "partial_or_better": sum(1 for result in results if result["judge"].get("label") in {"exact", "acceptable", "partial"}),
    }


def write_checkpoint(
    out_dir: Path,
    *,
    masked_inputs: list[dict[str, Any]],
    results: list[dict[str, Any]],
    rows: list[int],
    collection_name: str,
    qdrant_path: Path,
    target_namespace: str,
    layers: list[str],
    room_context: str | None,
    retrieval_mode: str,
) -> dict[str, Any]:
    summary = build_summary(
        results,
        rows=rows,
        collection_name=collection_name,
        qdrant_path=qdrant_path,
        target_namespace=target_namespace,
        layers=layers,
        room_context=room_context,
        retrieval_mode=retrieval_mode,
    )
    write_jsonl(out_dir / "masked_eval_inputs.jsonl", masked_inputs)
    write_jsonl(out_dir / "eval_results.jsonl", results)
    write_json(out_dir / "summary.json", summary)
    write_report(out_dir / "eval_report.md", results, summary)
    return summary


def run(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    target_namespace: str = DEFAULT_TARGET_NAMESPACE,
    rows: list[int] | None = None,
    collection_name: str = DEFAULT_COLLECTION,
    qdrant_path: Path | None = None,
    layers: list[str] | None = None,
    vector_top_k: int = 40,
    rerank_top_n: int = 10,
    embedding_endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    deepseek_url: str = DEFAULT_DEEPSEEK_URL,
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL,
    deepseek_api_key: str,
    room_context: str | None = None,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    resume: bool = False,
    timeout: int = 120,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = rows or DEFAULT_EVAL_ROWS
    layers = layers or DEFAULT_QUERY_LAYERS
    qdrant_path = qdrant_path or (STEP15_DIR / "qdrant")
    if retrieval_mode not in {"flat", "layered"}:
        raise ValueError(f"unsupported retrieval_mode: {retrieval_mode}")

    eval_items = select_eval_items(rows)
    retriever = QdrantRetriever(
        qdrant_path=qdrant_path,
        collection_name=collection_name,
        embedding_endpoint=embedding_endpoint,
        embedding_model=embedding_model,
    )
    reranker = RerankClient()

    masked_inputs: list[dict[str, Any]] = read_jsonl(out_dir / "masked_eval_inputs.jsonl") if resume else []
    results: list[dict[str, Any]] = read_jsonl(out_dir / "eval_results.jsonl") if resume else []
    completed_rows = {int(result["row_index"]) for result in results}
    try:
        for item in eval_items:
            if int(item["row_index"]) in completed_rows:
                print(f"qdrant skipped row {item['row_index']}: already completed")
                continue
            heldout_answer = item.get("existing_value") or ""
            base_query_text = build_masked_query(item, target_namespace)
            query_text = add_room_context(base_query_text, room_context)
            masked_inputs.append(
                {
                    "form_item_id": item["form_item_id"],
                    "row_index": item["row_index"],
                    "target_cell": item["target_cell"],
                    "question_text": item.get("question_text"),
                    "instruction_text": item.get("instruction_text"),
                    "answer_example_format_only": item.get("answer_example"),
                    "external_room_context": display_text(room_context),
                    "query_text": query_text,
                    "namespace_filter": [target_namespace, "global"],
                    "layer_filter": layers,
                }
            )
            if retrieval_mode == "layered":
                reranked_hits, vector_hits = layered_rerank_hits(
                    query_text,
                    retriever=retriever,
                    target_namespace=target_namespace,
                    allowed_layers=layers,
                    reranker=reranker,
                )
            else:
                vector_hits = retriever.search(
                    query_text,
                    namespaces=[target_namespace, "global"],
                    layers=layers,
                    top_k=vector_top_k,
                )
                reranked_hits = rerank_hits(query_text, vector_hits, rerank_top_n, reranker)
            generated = call_deepseek_json(
                url=deepseek_url,
                model=deepseek_model,
                api_key=deepseek_api_key,
                messages=build_qdrant_answer_messages(item, query_text, reranked_hits, room_context=room_context),
                timeout=timeout,
            )
            judge = call_deepseek_json(
                url=deepseek_url,
                model=deepseek_model,
                api_key=deepseek_api_key,
                messages=build_judge_messages(item, generated, heldout_answer),
                timeout=timeout,
            )
            results.append(
                {
                    "row_index": item["row_index"],
                    "target_cell": item["target_cell"],
                    "category_path": item.get("category_path") or [],
                    "question_text": item.get("question_text"),
                    "instruction_text": item.get("instruction_text"),
                    "answer_example_format_only": item.get("answer_example"),
                    "external_room_context": display_text(room_context),
                    "heldout_answer": heldout_answer,
                    "masked_query": query_text,
                    "namespace_filter": [target_namespace, "global"],
                    "layer_filter": layers,
                    "generated_answer": generated,
                    "judge": judge,
                    "top_hits": reranked_hits,
                    "vector_hits": vector_hits[:10],
                }
            )
            print(f"qdrant evaluated row {item['row_index']}: {judge.get('label')} score={judge.get('score')}")
            write_checkpoint(
                out_dir,
                masked_inputs=masked_inputs,
                results=results,
                rows=rows,
                collection_name=collection_name,
                qdrant_path=qdrant_path,
                target_namespace=target_namespace,
                layers=layers,
                room_context=room_context,
                retrieval_mode=retrieval_mode,
            )
    finally:
        retriever.close()

    return write_checkpoint(
        out_dir,
        masked_inputs=masked_inputs,
        results=results,
        rows=rows,
        collection_name=collection_name,
        qdrant_path=qdrant_path,
        target_namespace=target_namespace,
        layers=layers,
        room_context=room_context,
        retrieval_mode=retrieval_mode,
    )


def hit_summary(hit: dict[str, Any]) -> str:
    return display_text(
        " / ".join(
            part
            for part in [
                hit.get("file_name"),
                hit.get("anchor"),
                hit.get("raw_text"),
            ]
            if part
        )
    )


def generated_reference_summary(generated: dict[str, Any], hits: list[dict[str, Any]]) -> str:
    references = generated.get("reference_source_documents")
    if isinstance(references, list) and references:
        parts: list[str] = []
        for reference in references[:3]:
            if not isinstance(reference, dict):
                continue
            file_name = reference.get("file_name") or ""
            anchor = reference.get("anchor") or ""
            reason = reference.get("reason") or ""
            parts.append(display_text(" / ".join(part for part in [file_name, anchor, reason] if part)))
        if parts:
            return "；".join(parts)
    source_chunk_ids = generated.get("source_chunk_ids")
    if isinstance(source_chunk_ids, list) and source_chunk_ids:
        by_chunk_id = {hit.get("chunk_id"): hit for hit in hits}
        parts = [hit_summary(by_chunk_id[chunk_id]) for chunk_id in source_chunk_ids if chunk_id in by_chunk_id]
        parts = [part for part in parts if part]
        if parts:
            return "；".join(parts)
    fallback_hit = hits[0] if hits else {}
    return hit_summary(fallback_hit)



def write_report(path: Path, results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 15 Qdrant 全量库闭卷评估\n")
    lines.append("G 列 `机房信息` 是 held-out answer，只在评估阶段使用，不进入检索或答案生成。\n")
    lines.append("## 总览\n")
    lines.append(f"- 检索器：`{summary['retriever']}`")
    lines.append(f"- collection：`{summary['collection_name']}`")
    lines.append(f"- retrieval mode：`{summary.get('retrieval_mode', 'flat')}`")
    lines.append(f"- namespace filter：`{', '.join(summary['namespace_filter'])}`")
    lines.append(f"- layer filter：`{', '.join(summary['layer_filter'])}`")
    if summary.get("external_room_context"):
        lines.append(f"- 外部目标机房上下文：`{summary['external_room_context']}`")
    lines.append(f"- 样本数：**{summary['sample_count']}**")
    lines.append(f"- 平均分：**{summary['average_score']}**")
    lines.append(f"- exact/acceptable：**{summary['acceptable_or_better']} / {summary['sample_count']}**")
    lines.append(f"- partial 以上：**{summary['partial_or_better']} / {summary['sample_count']}**\n")
    lines.append("## 明细\n")
    lines.append("| row | question | status | generated | heldout | judge | score | source/ref | note |")
    lines.append("|---:|---|---|---|---|---|---:|---|---|")
    for result in results:
        generated = result["generated_answer"]
        judge = result["judge"]
        hits = result["top_hits"] if result["top_hits"] else []
        score = judge.get("score")
        lines.append(
            f"| {result['row_index']} | {md(result['question_text'], 50)} | "
            f"`{md(generated.get('answer_status'), 28)}` | "
            f"{md(generated.get('answer_value'), 90)} | {md(result['heldout_answer'], 90)} | "
            f"`{judge.get('label')}` | {score} | "
            f"{md(generated_reference_summary(generated, hits), 120)} | "
            f"{md(judge.get('reason'), 90)} |"
        )
    lines.append("\n## 遮蔽输入样例\n")
    for result in results[:3]:
        lines.append(f"### Row {result['row_index']}\n")
        lines.append("```text")
        lines.append(result["masked_query"])
        lines.append("```\n")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate base cloud form with full Qdrant store.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--target-namespace", default=DEFAULT_TARGET_NAMESPACE)
    parser.add_argument("--rows", default=",".join(str(row) for row in DEFAULT_EVAL_ROWS))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--qdrant-path", type=Path, default=STEP15_DIR / "qdrant")
    parser.add_argument("--layers", default=",".join(DEFAULT_QUERY_LAYERS))
    parser.add_argument("--vector-top-k", type=int, default=40)
    parser.add_argument("--rerank-top-n", type=int, default=10)
    parser.add_argument("--embedding-endpoint", default=DEFAULT_EMBEDDING_ENDPOINT)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--deepseek-url", default=DEFAULT_DEEPSEEK_URL)
    parser.add_argument("--deepseek-model", default=DEFAULT_DEEPSEEK_MODEL)
    parser.add_argument("--deepseek-api-key", default="")
    parser.add_argument("--room-context", default="")
    parser.add_argument("--retrieval-mode", choices=["flat", "layered"], default=DEFAULT_RETRIEVAL_MODE)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if not args.deepseek_api_key:
        raise RuntimeError("--deepseek-api-key is required")
    rows = all_base_cloud_rows() if args.rows.strip().lower() == "all" else [int(part) for part in args.rows.split(",") if part.strip()]
    layers = [part.strip() for part in args.layers.split(",") if part.strip()]
    summary = run(
        out_dir=args.out_dir,
        target_namespace=args.target_namespace,
        rows=rows,
        collection_name=args.collection,
        qdrant_path=args.qdrant_path,
        layers=layers,
        vector_top_k=args.vector_top_k,
        rerank_top_n=args.rerank_top_n,
        embedding_endpoint=args.embedding_endpoint,
        embedding_model=args.embedding_model,
        deepseek_url=args.deepseek_url,
        deepseek_model=args.deepseek_model,
        deepseek_api_key=args.deepseek_api_key,
        room_context=args.room_context,
        retrieval_mode=args.retrieval_mode,
        resume=args.resume,
        timeout=args.timeout,
    )
    print(
        f"qdrant evaluated {summary['sample_count']}/{summary['requested_sample_count']} masked rows: "
        f"avg={summary['average_score']}, labels={summary['label_counts']}"
    )


if __name__ == "__main__":
    main()
