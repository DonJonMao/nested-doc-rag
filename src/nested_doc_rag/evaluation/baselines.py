from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from nested_doc_rag.config import load_app_config
from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.gongkan_eval import (
    BASE_CLOUD_FILE,
    build_judge_messages,
    build_masked_query,
    call_deepseek_json,
    select_eval_items,
)
from nested_doc_rag.io import display_text, md, read_jsonl, write_json, write_jsonl
from nested_doc_rag.retrieval import QdrantRetriever, layered_rerank_hits, rerank_hits

DEFAULT_CONFIG = load_app_config()
PROJECT_ROOT = DEFAULT_CONFIG.paths.project_root
DEFAULT_OUT_DIR = DEFAULT_CONFIG.paths.artifacts_dir / "15_vector_store/base_cloud_closed_book_eval"
STEP12_DIR = DEFAULT_CONFIG.paths.artifacts_dir / "12_gongkan_form_analysis"
STEP15_DIR = DEFAULT_CONFIG.paths.artifacts_dir / "15_vector_store"
DEFAULT_COLLECTION = DEFAULT_CONFIG.qdrant.collection_name
DEFAULT_TARGET_NAMESPACE = DEFAULT_CONFIG.retrieval.target_namespace
DEFAULT_EVAL_ROWS = DEFAULT_CONFIG.evaluation.default_rows
DEFAULT_QUERY_LAYERS = DEFAULT_CONFIG.retrieval.query_layers
DEFAULT_RETRIEVAL_MODE = DEFAULT_CONFIG.retrieval.retrieval_mode
DEFAULT_EMBEDDING_ENDPOINT = DEFAULT_CONFIG.services.embedding_endpoint
DEFAULT_EMBEDDING_MODEL = DEFAULT_CONFIG.services.embedding_model
DEFAULT_RERANK_ENDPOINT = DEFAULT_CONFIG.services.rerank_endpoint
DEFAULT_RERANK_MODEL = DEFAULT_CONFIG.services.rerank_model
DEFAULT_DEEPSEEK_URL = DEFAULT_CONFIG.services.chat_endpoint
DEFAULT_DEEPSEEK_MODEL = DEFAULT_CONFIG.services.chat_model
DEFAULT_CHAT_API_KEY_ENV = DEFAULT_CONFIG.services.chat_api_key_env
DEFAULT_TIMEOUT = DEFAULT_CONFIG.evaluation.timeout_seconds

LAYERED_RETRIEVAL_PLAN = DEFAULT_CONFIG.retrieval.layered_plan


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


def build_summary(
    results: list[dict[str, Any]],
    *,
    rows: list[int],
    collection_name: str,
    qdrant_path: Path,
    target_namespace: str,
    global_namespace: str,
    layers: list[str],
    room_context: str | None,
    retrieval_mode: str,
    layered_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    label_counts = Counter(result["judge"].get("label") for result in results)
    status_counts = Counter((result.get("generated_answer") or {}).get("answer_status") for result in results)
    numeric_scores = [float(result["judge"].get("score") or 0) for result in results]
    return {
        "retriever": "qdrant_full_store",
        "collection_name": collection_name,
        "qdrant_path": str(qdrant_path),
        "target_namespace": target_namespace,
        "namespace_filter": [target_namespace, global_namespace],
        "layer_filter": layers,
        "retrieval_mode": retrieval_mode,
        "layered_retrieval_plan": layered_plan if retrieval_mode == "layered" else [],
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
    global_namespace: str,
    layers: list[str],
    room_context: str | None,
    retrieval_mode: str,
    layered_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = build_summary(
        results,
        rows=rows,
        collection_name=collection_name,
        qdrant_path=qdrant_path,
        target_namespace=target_namespace,
        global_namespace=global_namespace,
        layers=layers,
        room_context=room_context,
        retrieval_mode=retrieval_mode,
        layered_plan=layered_plan,
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
    global_namespace: str = DEFAULT_CONFIG.retrieval.global_namespace,
    rows: list[int] | None = None,
    collection_name: str = DEFAULT_COLLECTION,
    qdrant_path: Path | None = None,
    qdrant_prefer_grpc: bool = DEFAULT_CONFIG.qdrant.prefer_grpc,
    qdrant_timeout: int = DEFAULT_CONFIG.qdrant.timeout,
    layers: list[str] | None = None,
    vector_top_k: int = 40,
    rerank_top_n: int = 10,
    embedding_endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    rerank_endpoint: str = DEFAULT_RERANK_ENDPOINT,
    rerank_model: str = DEFAULT_RERANK_MODEL,
    deepseek_url: str = DEFAULT_DEEPSEEK_URL,
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL,
    deepseek_api_key: str,
    form_items_path: Path | None = None,
    room_context: str | None = None,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    layered_plan: list[dict[str, Any]] | None = None,
    resume: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = rows or DEFAULT_EVAL_ROWS
    layers = layers or DEFAULT_QUERY_LAYERS
    qdrant_path = qdrant_path or (STEP15_DIR / "qdrant")
    form_items_path = form_items_path or (STEP12_DIR / "form_items.jsonl")
    layered_plan = layered_plan or LAYERED_RETRIEVAL_PLAN
    if retrieval_mode not in {"flat", "layered"}:
        raise ValueError(f"unsupported retrieval_mode: {retrieval_mode}")

    eval_items = select_eval_items(rows, form_items_path=form_items_path)
    retriever = QdrantRetriever(
        qdrant_path=qdrant_path,
        collection_name=collection_name,
        embedding_endpoint=embedding_endpoint,
        embedding_model=embedding_model,
        prefer_grpc=qdrant_prefer_grpc,
        timeout=qdrant_timeout,
    )
    reranker = RerankClient(endpoint=rerank_endpoint, model=rerank_model, timeout_seconds=timeout)

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
                    "namespace_filter": [target_namespace, global_namespace],
                    "layer_filter": layers,
                }
            )
            if retrieval_mode == "layered":
                reranked_hits, vector_hits = layered_rerank_hits(
                    query_text,
                    retriever=retriever,
                    target_namespace=target_namespace,
                    global_namespace=global_namespace,
                    allowed_layers=layers,
                    reranker=reranker,
                    layered_plan=layered_plan,
                )
            else:
                vector_hits = retriever.search(
                    query_text,
                    namespaces=[target_namespace, global_namespace],
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
                    "namespace_filter": [target_namespace, global_namespace],
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
                global_namespace=global_namespace,
                layers=layers,
                room_context=room_context,
                retrieval_mode=retrieval_mode,
                layered_plan=layered_plan,
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
        global_namespace=global_namespace,
        layers=layers,
        room_context=room_context,
        retrieval_mode=retrieval_mode,
        layered_plan=layered_plan,
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
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--target-namespace", default=None)
    parser.add_argument("--global-namespace", default=None)
    parser.add_argument("--rows", default=None)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--qdrant-path", type=Path, default=None)
    parser.add_argument("--layers", default=None)
    parser.add_argument("--vector-top-k", type=int, default=None)
    parser.add_argument("--rerank-top-n", type=int, default=None)
    parser.add_argument("--embedding-endpoint", default=None)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--rerank-endpoint", default=None)
    parser.add_argument("--rerank-model", default=None)
    parser.add_argument("--deepseek-url", default=None)
    parser.add_argument("--deepseek-model", default=None)
    parser.add_argument("--deepseek-api-key", default="")
    parser.add_argument("--deepseek-api-key-env", default=None)
    parser.add_argument("--room-context", default="")
    parser.add_argument("--retrieval-mode", choices=["flat", "layered"], default=None)
    parser.add_argument("--resume", action="store_true", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args()
    config = load_app_config(args.config)
    api_key_env = args.deepseek_api_key_env or config.services.chat_api_key_env
    api_key = args.deepseek_api_key or os.environ.get(api_key_env, "")
    if not api_key:
        raise RuntimeError(f"--deepseek-api-key is required, or set ${api_key_env}")

    out_dir = args.out_dir or (config.paths.artifacts_dir / "15_vector_store/base_cloud_closed_book_eval")
    step12_dir = config.paths.artifacts_dir / "12_gongkan_form_analysis"
    rows_text = args.rows or ",".join(str(row) for row in config.evaluation.default_rows)
    rows = all_base_cloud_rows(step12_dir) if rows_text.strip().lower() == "all" else [int(part) for part in rows_text.split(",") if part.strip()]
    layers_text = args.layers or ",".join(config.retrieval.query_layers)
    layers = [part.strip() for part in layers_text.split(",") if part.strip()]
    summary = run(
        out_dir=out_dir,
        target_namespace=args.target_namespace or config.retrieval.target_namespace,
        global_namespace=args.global_namespace or config.retrieval.global_namespace,
        rows=rows,
        collection_name=args.collection or config.qdrant.collection_name,
        qdrant_path=args.qdrant_path or config.paths.qdrant_path,
        qdrant_prefer_grpc=config.qdrant.prefer_grpc,
        qdrant_timeout=config.qdrant.timeout,
        layers=layers,
        vector_top_k=args.vector_top_k or config.retrieval.vector_top_k,
        rerank_top_n=args.rerank_top_n or config.retrieval.rerank_top_n,
        embedding_endpoint=args.embedding_endpoint or config.services.embedding_endpoint,
        embedding_model=args.embedding_model or config.services.embedding_model,
        rerank_endpoint=args.rerank_endpoint or config.services.rerank_endpoint,
        rerank_model=args.rerank_model or config.services.rerank_model,
        deepseek_url=args.deepseek_url or config.services.chat_endpoint,
        deepseek_model=args.deepseek_model or config.services.chat_model,
        deepseek_api_key=api_key,
        form_items_path=step12_dir / "form_items.jsonl",
        room_context=args.room_context,
        retrieval_mode=args.retrieval_mode or config.retrieval.retrieval_mode,
        layered_plan=config.retrieval.layered_plan,
        resume=config.evaluation.resume if args.resume is None else bool(args.resume),
        timeout=args.timeout or config.evaluation.timeout_seconds,
    )
    print(
        f"qdrant evaluated {summary['sample_count']}/{summary['requested_sample_count']} masked rows: "
        f"avg={summary['average_score']}, labels={summary['label_counts']}"
    )


if __name__ == "__main__":
    main()
