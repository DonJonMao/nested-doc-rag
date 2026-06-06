from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from array import array
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/Users/mao/projects/datacenter")
STEP11_DIR = PROJECT_ROOT / "artifacts/11_embedding_build"
STEP12_DIR = PROJECT_ROOT / "artifacts/12_gongkan_form_analysis"
DEFAULT_OUT_DIR = PROJECT_ROOT / "artifacts/14_gongkan_rag_eval"

sys.path.insert(0, str(PROJECT_ROOT / "11_embedding_build"))
from embedding_pipeline import EmbeddingClient, RerankClient  # noqa: E402


DEFAULT_DEEPSEEK_URL = "http://111.19.156.30:8006/v1/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"

BASE_CLOUD_FILE = "基地云机房信息调研表.xlsx"
DEFAULT_TARGET_NAMESPACE = "xixian_4"
DEFAULT_EVAL_ROWS = [4, 5, 13, 16, 25, 26, 31, 36, 53, 117]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def display_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if limit and len(text) > limit:
        return text[: limit - 1] + "..."
    return text


def md(value: Any, limit: int = 120) -> str:
    return display_text(value, limit).replace("|", "\\|")


def select_eval_items(rows: list[int]) -> list[dict[str, Any]]:
    items = [
        item
        for item in read_jsonl(STEP12_DIR / "form_items.jsonl")
        if item.get("file_name") == BASE_CLOUD_FILE and int(item.get("row_index")) in set(rows)
    ]
    by_row = {int(item["row_index"]): item for item in items}
    missing = [row for row in rows if row not in by_row]
    if missing:
        raise RuntimeError(f"missing base cloud form rows: {missing}")
    return [by_row[row] for row in rows]


def select_index_records(target_namespace: str, include_global: bool = True) -> list[dict[str, Any]]:
    namespaces = {target_namespace}
    if include_global:
        namespaces.add("global")
    return [
        record
        for record in read_jsonl(STEP11_DIR / "ingestion_manifest.jsonl")
        if record.get("default_index") and record.get("namespace") in namespaces and record.get("corpus_layer") in {"fact", "evidence"}
    ]


def build_eval_index(
    records: list[dict[str, Any]],
    out_dir: Path,
    *,
    rebuild: bool,
    batch_size: int,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "eval_index_meta.json"
    embeddings_path = out_dir / "eval_index_embeddings.f32"
    records_path = out_dir / "eval_index_records.jsonl"
    if not rebuild and meta_path.exists() and embeddings_path.exists() and records_path.exists():
        return read_json(meta_path)

    client = EmbeddingClient()
    vectors = array("f")
    dimension: int | None = None
    started = time.time()
    for batch_no, start in enumerate(range(0, len(records), batch_size), 1):
        batch = records[start : start + batch_size]
        embeddings = client.embed([record["text_for_embedding"] for record in batch])
        for vector in embeddings:
            if dimension is None:
                dimension = len(vector)
            if len(vector) != dimension:
                raise RuntimeError(f"embedding dimension changed: {dimension} -> {len(vector)}")
            vectors.extend(float(value) for value in vector)
        print(f"eval index embedded batch {batch_no}: {len(batch)} records")

    if dimension is None:
        raise RuntimeError("no records to embed")
    with embeddings_path.open("wb") as f:
        vectors.tofile(f)
    write_jsonl(records_path, records)
    meta = {
        "record_count": len(records),
        "dimension": dimension,
        "records_path": str(records_path),
        "embeddings_path": str(embeddings_path),
        "namespaces": sorted(set(record["namespace"] for record in records)),
        "batch_size": batch_size,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    write_json(meta_path, meta)
    return meta


def load_eval_index(out_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], array]:
    meta = read_json(out_dir / "eval_index_meta.json")
    records = read_jsonl(Path(meta["records_path"]))
    vectors = array("f")
    with Path(meta["embeddings_path"]).open("rb") as f:
        vectors.fromfile(f, meta["record_count"] * meta["dimension"])
    return meta, records, vectors


def cosine_search(query_vector: list[float], records: list[dict[str, Any]], vectors: array, dimension: int, top_k: int) -> list[dict[str, Any]]:
    q_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
    scored: list[tuple[float, int]] = []
    for index, record in enumerate(records):
        start = index * dimension
        dot = 0.0
        d_norm_sq = 0.0
        for offset, q_value in enumerate(query_vector):
            value = vectors[start + offset]
            dot += q_value * value
            d_norm_sq += value * value
        score = dot / (q_norm * (math.sqrt(d_norm_sq) or 1.0))
        score *= float(record.get("rank_boost") or 1.0)
        scored.append((score, index))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[dict[str, Any]] = []
    for rank, (score, index) in enumerate(scored[:top_k], 1):
        record = records[index]
        hits.append(
            {
                "vector_rank": rank,
                "vector_score": round(score, 6),
                "chunk_id": record["chunk_id"],
                "namespace": record["namespace"],
                "source_type": record["source_type"],
                "corpus_layer": record["corpus_layer"],
                "anchor": record.get("anchor"),
                "file_name": record.get("file_name"),
                "raw_text": record.get("raw_text"),
                "text_for_embedding": record.get("text_for_embedding"),
                "proof_attachment_ids": record.get("proof_attachment_ids") or [],
            }
        )
    return hits


def build_masked_query(item: dict[str, Any], target_namespace: str) -> str:
    parts = [
        f"目标机房：{target_namespace}",
        "任务：为基地云机房信息调研表生成最后一列“机房信息”的候选答案",
        f"类别：{' / '.join(item.get('category_path') or [])}",
        f"指标名称：{item.get('question_text')}",
    ]
    if item.get("instruction_text"):
        parts.append(f"填写说明及标准：{item['instruction_text']}")
    if item.get("answer_example"):
        parts.append(f"机房信息示例仅作格式参考，不是答案：{item['answer_example']}")
    if item.get("needs_evidence"):
        parts.append("该项需要证明材料或截图佐证；如命中附件，只返回附件标记，不做 OCR")
    parts.append("只能使用知识库检索结果；找不到就返回未找到")
    return "。".join(display_text(part).rstrip("。") for part in parts if display_text(part)) + "。"


def call_deepseek_json(
    *,
    url: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    timeout: int,
) -> dict[str, Any]:
    payload = {"model": model, "temperature": 0, "messages": messages}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [
                "curl",
                "--noproxy",
                "*",
                "-sS",
                "--max-time",
                str(timeout),
                "-X",
                "POST",
                url,
                "-H",
                "Content-Type: application/json",
                "-H",
                f"Authorization: Bearer {api_key}",
                "-d",
                f"@{tmp_path}",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip() or proc.stdout.strip()}")
    response = json.loads(proc.stdout)
    content = response["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    if not content.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            content = match.group(0)
    return json.loads(content)


def build_answer_messages(item: dict[str, Any], query_text: str, hits: list[dict[str, Any]]) -> list[dict[str, str]]:
    evidence = [
        {
            "chunk_id": hit["chunk_id"],
            "rank": hit.get("rerank_rank", hit.get("vector_rank")),
            "score": hit.get("rerank_score", hit.get("vector_score")),
            "namespace": hit["namespace"],
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
    }
    schema = {
        "answer_value": "只来自 retrieved_chunks 的短答案；找不到填“未找到”",
        "confidence": "0-1",
        "source_chunk_ids": ["chunk id"],
        "evidence_attachment_ids": ["attachment id"],
        "missing_fields": ["缺失字段"],
        "notes": "边界说明",
    }
    user_prompt = (
        "下面是一个工勘单填报项和 RAG 检索结果。请只用 retrieved_chunks 中的原文生成答案，不能使用常识，不能使用表格最后一列答案。\n"
        "answer_example_format_only 只能作为格式参考，不能作为事实来源。图片附件只作为证据标记，不 OCR。\n"
        "如果 retrieved_chunks 没有明确证据，answer_value 必须是“未找到”。\n\n"
        f"masked_query:\n{query_text}\n\n"
        f"form_item_without_heldout_answer:\n{json.dumps(item_view, ensure_ascii=False, indent=2)}\n\n"
        f"retrieved_chunks:\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
        "请只输出严格 JSON，schema 如下：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )
    return [
        {"role": "system", "content": "你是受约束的 RAG 答案整理器，只能使用检索证据，必须输出 JSON。"},
        {"role": "user", "content": user_prompt},
    ]


def build_judge_messages(item: dict[str, Any], generated: dict[str, Any], heldout_answer: str) -> list[dict[str, str]]:
    schema = {
        "label": "exact | acceptable | partial | mismatch | not_found_expected",
        "score": "0-1",
        "reason": "简短中文说明",
    }
    content = (
        "你是 RAG 评估器。比较 generated_answer 与 heldout_answer 是否语义一致。"
        "允许单位、空格、大小写、顺序的轻微差异；如果答案覆盖了核心事实但缺少细节，判 partial。"
        "如果 heldout_answer 本身是“无法提供/不涉及/否/是”等，也按语义判断。\n\n"
        f"question: {item.get('question_text')}\n"
        f"instruction: {item.get('instruction_text')}\n"
        f"generated_answer: {json.dumps(generated, ensure_ascii=False)}\n"
        f"heldout_answer: {heldout_answer}\n\n"
        f"只输出 JSON：{json.dumps(schema, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": "你只做答案一致性评估，必须输出 JSON。"},
        {"role": "user", "content": content},
    ]


def run(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    target_namespace: str = DEFAULT_TARGET_NAMESPACE,
    rows: list[int] | None = None,
    rebuild_index: bool = False,
    batch_size: int = 32,
    vector_top_k: int = 30,
    rerank_top_n: int = 8,
    deepseek_url: str = DEFAULT_DEEPSEEK_URL,
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL,
    deepseek_api_key: str,
    timeout: int = 120,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = rows or DEFAULT_EVAL_ROWS
    eval_items = select_eval_items(rows)
    index_records = select_index_records(target_namespace, include_global=True)
    index_meta = build_eval_index(index_records, out_dir, rebuild=rebuild_index, batch_size=batch_size)
    meta, records, vectors = load_eval_index(out_dir)

    embedder = EmbeddingClient()
    reranker = RerankClient()
    results: list[dict[str, Any]] = []
    masked_inputs: list[dict[str, Any]] = []

    for item in eval_items:
        heldout_answer = item.get("existing_value") or ""
        query_text = build_masked_query(item, target_namespace)
        masked_input = {
            "form_item_id": item["form_item_id"],
            "row_index": item["row_index"],
            "target_cell": item["target_cell"],
            "question_text": item.get("question_text"),
            "instruction_text": item.get("instruction_text"),
            "answer_example_format_only": item.get("answer_example"),
            "query_text": query_text,
        }
        masked_inputs.append(masked_input)

        query_vector = embedder.embed_query(query_text)
        vector_hits = cosine_search(query_vector, records, vectors, int(meta["dimension"]), top_k=vector_top_k)
        reranked = reranker.rerank(query_text, [hit["text_for_embedding"] for hit in vector_hits], top_n=rerank_top_n)
        reranked_hits: list[dict[str, Any]] = []
        for rank, rerank_item in enumerate(reranked, 1):
            hit = dict(vector_hits[int(rerank_item["index"])])
            hit["rerank_rank"] = rank
            hit["rerank_score"] = rerank_item.get("relevance_score")
            reranked_hits.append(hit)

        generated = call_deepseek_json(
            url=deepseek_url,
            model=deepseek_model,
            api_key=deepseek_api_key,
            messages=build_answer_messages(item, query_text, reranked_hits),
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
                "heldout_answer": heldout_answer,
                "masked_query": query_text,
                "generated_answer": generated,
                "judge": judge,
                "top_hits": reranked_hits,
            }
        )
        print(f"evaluated row {item['row_index']}: {judge.get('label')} score={judge.get('score')}")

    label_counts = Counter(result["judge"].get("label") for result in results)
    numeric_scores = [float(result["judge"].get("score") or 0) for result in results]
    summary = {
        "target_namespace": target_namespace,
        "rows": rows,
        "sample_count": len(results),
        "index_record_count": index_meta["record_count"],
        "index_namespaces": index_meta["namespaces"],
        "answer_leakage_control": "heldout_answer/G列机房信息不进入 masked_query、answer prompt、embedding 或 rerank，只在 judge 阶段使用。",
        "label_counts": dict(label_counts),
        "average_score": round(sum(numeric_scores) / len(numeric_scores), 4) if numeric_scores else 0,
        "acceptable_or_better": sum(1 for result in results if result["judge"].get("label") in {"exact", "acceptable"}),
        "partial_or_better": sum(1 for result in results if result["judge"].get("label") in {"exact", "acceptable", "partial"}),
    }
    write_jsonl(out_dir / "masked_eval_inputs.jsonl", masked_inputs)
    write_jsonl(out_dir / "eval_results.jsonl", results)
    write_json(out_dir / "summary.json", summary)
    write_report(out_dir / "eval_report.md", results, summary)
    return summary


def write_report(path: Path, results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 14 基地云机房信息调研表 RAG 闭卷评估\n")
    lines.append("G 列 `机房信息` 被作为 held-out answer，只在评估阶段使用，不进入检索或答案生成。\n")
    lines.append("## 总览\n")
    lines.append(f"- 目标分库：`{summary['target_namespace']} + global`")
    lines.append(f"- 样本数：**{summary['sample_count']}**")
    lines.append(f"- 评估索引记录数：**{summary['index_record_count']}**")
    lines.append(f"- 平均分：**{summary['average_score']}**")
    lines.append(f"- exact/acceptable：**{summary['acceptable_or_better']} / {summary['sample_count']}**")
    lines.append(f"- partial 以上：**{summary['partial_or_better']} / {summary['sample_count']}**\n")
    lines.append("## 明细\n")
    lines.append("| row | question | generated | heldout | judge | score | top source | note |")
    lines.append("|---:|---|---|---|---|---:|---|---|")
    for result in results:
        generated = result["generated_answer"]
        judge = result["judge"]
        top = result["top_hits"][0] if result["top_hits"] else {}
        lines.append(
            f"| {result['row_index']} | {md(result['question_text'], 50)} | "
            f"{md(generated.get('answer_value'), 90)} | {md(result['heldout_answer'], 90)} | "
            f"`{judge.get('label')}` | {judge.get('score')} | "
            f"{md(top.get('anchor'), 50)}: {md(top.get('raw_text'), 90)} | "
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
    parser = argparse.ArgumentParser(description="Evaluate 10 masked rows from 基地云机房信息调研表 with RAG.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--target-namespace", default=DEFAULT_TARGET_NAMESPACE)
    parser.add_argument("--rows", default=",".join(str(row) for row in DEFAULT_EVAL_ROWS))
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--vector-top-k", type=int, default=30)
    parser.add_argument("--rerank-top-n", type=int, default=8)
    parser.add_argument("--deepseek-url", default=DEFAULT_DEEPSEEK_URL)
    parser.add_argument("--deepseek-model", default=DEFAULT_DEEPSEEK_MODEL)
    parser.add_argument("--deepseek-api-key", default="")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    api_key = args.deepseek_api_key
    if not api_key:
        raise RuntimeError("--deepseek-api-key is required for answer generation and judging")
    rows = [int(part) for part in args.rows.split(",") if part.strip()]
    summary = run(
        out_dir=args.out_dir,
        target_namespace=args.target_namespace,
        rows=rows,
        rebuild_index=args.rebuild_index,
        batch_size=args.batch_size,
        vector_top_k=args.vector_top_k,
        rerank_top_n=args.rerank_top_n,
        deepseek_url=args.deepseek_url,
        deepseek_model=args.deepseek_model,
        deepseek_api_key=api_key,
        timeout=args.timeout,
    )
    print(
        f"evaluated {summary['sample_count']} masked rows: "
        f"avg={summary['average_score']}, labels={summary['label_counts']}"
    )


if __name__ == "__main__":
    main()
