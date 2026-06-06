from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from array import array
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from nested_doc_rag.config import load_app_config
from nested_doc_rag.embedding import QUERY_INSTRUCTION, EmbeddingClient, RerankClient

DEFAULT_CONFIG = load_app_config()
PROJECT_ROOT = DEFAULT_CONFIG.paths.project_root
STEP05_SEGMENTS = PROJECT_ROOT / "artifacts/05_segment_extract/segments.jsonl"
STEP09_SEGMENTS = PROJECT_ROOT / "artifacts/09_table_candidate_resolution/resolved_table_segments.jsonl"
STEP10_AUDIT = PROJECT_ROOT / "artifacts/10_semantic_segment_audit/semantic_audit.jsonl"
DEFAULT_OUT_DIR = DEFAULT_CONFIG.paths.artifacts_dir / "11_embedding_build"

DEFAULT_EMBEDDING_ENDPOINT = DEFAULT_CONFIG.services.embedding_endpoint
DEFAULT_EMBEDDING_MODEL = DEFAULT_CONFIG.services.embedding_model
DEFAULT_RERANK_ENDPOINT = DEFAULT_CONFIG.services.rerank_endpoint
DEFAULT_RERANK_MODEL = DEFAULT_CONFIG.services.rerank_model

DEFAULT_INDEX_POLICIES = {
    "embed",
    "embed_preferred",
    "embed_preferred_and_image",
    "embed_with_parent",
    "embed_with_parent_and_image",
    "embed_with_image_evidence",
}

SMOKE_KEYWORDS = [
    "停电",
    "油机",
    "发电",
    "零信任",
    "4A",
    "路由故障",
    "故障排查",
    "温湿度",
    "冷通道",
    "IP地址",
    "客户路由",
]

SMOKE_KEYWORD_GROUPS = {
    "power": ["停电", "油机", "发电"],
    "auth": ["零信任", "4A"],
    "fault": ["路由故障", "故障排查", "客户路由"],
    "environment": ["温湿度", "冷通道"],
}

SMOKE_QUERIES = [
    {
        "query": "通信机房停电后如何安排油机发电？",
        "namespaces": ["global", "xianyang", "xixian_6", "xian", "chengdong_baqiao"],
    },
    {
        "query": "零信任登录4A怎么操作？",
        "namespaces": ["global", "xixian_6", "xianyang"],
    },
    {
        "query": "客户路由故障如何排查？",
        "namespaces": ["global", "xixian_2", "xixian_6", "xianyang"],
    },
    {
        "query": "机房温湿度监控有哪些记录或要求？",
        "namespaces": ["global", "xixian_6", "xianyang", "xian"],
    },
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def stable_id(*parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def compact_text(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split())
    if limit and len(text) > limit:
        text = text[: limit - 1] + "..."
    return text


def markdown_text(value: Any, limit: int = 120) -> str:
    return compact_text(value, limit).replace("|", "\\|")


def normalize_namespace(value: Any) -> str:
    namespace = compact_text(value)
    return namespace or "global"


def proof_attachment_ids(segment: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for attachment in segment.get("proof_attachments") or []:
        attachment_id = attachment.get("attachment_id")
        if attachment_id:
            ids.append(str(attachment_id))
    return ids


def corpus_layer_for_policy(policy: str) -> str:
    if policy == "metadata_only":
        return "meta"
    if policy == "embed_as_template":
        return "template"
    if policy.startswith("exclude"):
        return "excluded"
    if "image" in policy:
        return "evidence"
    return "fact"


def rank_boost_for_policy(policy: str) -> float:
    if policy.startswith("embed_preferred"):
        return 1.12
    if policy.startswith("embed_with_parent"):
        return 1.04
    return 1.0


def make_main_excel_manifest_record(segment: dict[str, Any]) -> dict[str, Any]:
    namespace = normalize_namespace(segment.get("data_center_id"))
    source_anchor = segment.get("source_anchor") or {}
    chunk_id = "rag_" + stable_id("main_excel_capability", segment.get("segment_id"))
    return {
        "chunk_id": chunk_id,
        "source_type": "main_excel_capability",
        "source_segment_id": segment.get("segment_id"),
        "namespace": namespace,
        "data_center_id": namespace,
        "corpus_layer": "fact",
        "embedding_policy": "embed",
        "default_index": True,
        "rank_boost": 1.0,
        "text_for_embedding": compact_text(segment.get("embedding_text") or segment.get("raw_text")),
        "raw_text": compact_text(segment.get("raw_text")),
        "file_name": segment.get("file_name"),
        "relative_path": segment.get("relative_path"),
        "sheet_name": segment.get("sheet_name"),
        "row_index": segment.get("row_index"),
        "anchor": f"{source_anchor.get('sheet_name') or segment.get('sheet_name')}!row {segment.get('row_index')}",
        "table_id": None,
        "parent_chunk_id": None,
        "parent_attachment_id": None,
        "embedded_file_name": None,
        "proof_attachment_ids": proof_attachment_ids(segment),
        "proof_attachment_count": segment.get("proof_attachment_count", 0),
        "proof_cell_refs": segment.get("proof_cell_refs") or [],
        "semantic_flags": [],
        "retrieval_tags": [
            "main_excel",
            "capability_row",
            namespace,
            *(segment.get("category_path") or []),
        ],
        "source": {
            "file_id": segment.get("file_id"),
            "file_name": segment.get("file_name"),
            "sheet_name": segment.get("sheet_name"),
            "row_index": segment.get("row_index"),
            "cell_range": source_anchor.get("cell_range"),
            "proof_cells": source_anchor.get("proof_cells") or [],
        },
    }


def make_embedded_table_manifest_record(
    segment: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    policy = audit.get("embedding_policy") or "embed"
    namespace = normalize_namespace(segment.get("data_center_id"))
    layer = corpus_layer_for_policy(policy)
    parent_attachment_id = segment.get("parent_attachment_id")
    semantic_flags = audit.get("semantic_flags") or []
    proof_ids: list[str] = []
    if parent_attachment_id and ("image" in policy or "needs_image_evidence" in semantic_flags):
        proof_ids.append(str(parent_attachment_id))

    chunk_id = "rag_" + stable_id("embedded_word_table", segment.get("segment_id"))
    return {
        "chunk_id": chunk_id,
        "source_type": "embedded_word_table",
        "source_segment_id": segment.get("segment_id"),
        "namespace": namespace,
        "data_center_id": namespace,
        "corpus_layer": layer,
        "embedding_policy": policy,
        "default_index": policy in DEFAULT_INDEX_POLICIES,
        "rank_boost": rank_boost_for_policy(policy),
        "text_for_embedding": compact_text(segment.get("embedding_text") or segment.get("raw_text")),
        "raw_text": compact_text(segment.get("raw_text")),
        "file_name": segment.get("file_name"),
        "relative_path": segment.get("file_name"),
        "sheet_name": segment.get("parent_sheet_name"),
        "row_index": segment.get("parent_source_cell"),
        "anchor": segment.get("anchor"),
        "table_id": segment.get("table_id"),
        "parent_chunk_id": segment.get("parent_segment_id"),
        "parent_attachment_id": parent_attachment_id,
        "embedded_file_name": segment.get("embedded_file_name"),
        "proof_attachment_ids": proof_ids,
        "proof_attachment_count": len(proof_ids),
        "proof_cell_refs": [segment.get("parent_source_cell")] if segment.get("parent_source_cell") else [],
        "semantic_flags": semantic_flags,
        "retrieval_tags": [
            "embedded_word_table",
            namespace,
            segment.get("table_category"),
            segment.get("segment_role"),
            segment.get("context"),
            segment.get("group"),
        ],
        "source": {
            "file_name": segment.get("file_name"),
            "sheet_name": segment.get("parent_sheet_name"),
            "source_cell": segment.get("parent_source_cell"),
            "anchor": segment.get("anchor"),
            "table_id": segment.get("table_id"),
            "table_category": segment.get("table_category"),
            "segment_role": segment.get("segment_role"),
            "source_row_indices": segment.get("source_row_indices") or [],
            "embedded_file_name": segment.get("embedded_file_name"),
            "semantic_status": audit.get("semantic_status"),
            "semantic_issue": audit.get("semantic_issue"),
        },
    }


def build_manifest(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    segments_path: Path = STEP05_SEGMENTS,
    resolved_segments_path: Path = STEP09_SEGMENTS,
    semantic_audit_path: Path = STEP10_AUDIT,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)

    audit_by_segment_id = {
        record.get("segment_id"): record
        for record in read_jsonl(semantic_audit_path)
        if record.get("segment_id")
    }

    records: list[dict[str, Any]] = []
    for segment in read_jsonl(segments_path):
        records.append(make_main_excel_manifest_record(segment))

    missing_audit = 0
    for segment in read_jsonl(resolved_segments_path):
        audit = audit_by_segment_id.get(segment.get("segment_id"))
        if not audit:
            missing_audit += 1
            audit = {"embedding_policy": "exclude_until_fixed", "semantic_status": "missing_step10_audit"}
        records.append(make_embedded_table_manifest_record(segment, audit))

    write_jsonl(out_dir / "ingestion_manifest.jsonl", records)

    summary = {
        "total_records": len(records),
        "default_index_records": sum(1 for record in records if record["default_index"]),
        "non_default_records": sum(1 for record in records if not record["default_index"]),
        "missing_step10_audit_records": missing_audit,
        "counts_by_source_type": dict(Counter(record["source_type"] for record in records)),
        "counts_by_namespace": dict(Counter(record["namespace"] for record in records)),
        "default_counts_by_namespace": dict(Counter(record["namespace"] for record in records if record["default_index"])),
        "counts_by_corpus_layer": dict(Counter(record["corpus_layer"] for record in records)),
        "counts_by_embedding_policy": dict(Counter(record["embedding_policy"] for record in records)),
        "embedding_input_rule": {
            "document": "直接使用 text_for_embedding，不加查询 instruction。",
            "query": f"使用前缀：{QUERY_INSTRUCTION!r}",
        },
    }
    write_json(out_dir / "manifest_summary.json", summary)
    write_manifest_visualization(out_dir / "visualization.md", records, summary)
    return summary


def write_manifest_visualization(path: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    default_records = [record for record in records if record["default_index"]]
    lines: list[str] = []
    lines.append("# Step 11 嵌入清单与本地向量索引\n")
    lines.append("## 清单策略\n")
    lines.append(f"- 清单总数：**{summary['total_records']}**")
    lines.append(f"- 默认进入向量索引：**{summary['default_index_records']}**")
    lines.append(f"- 仅作为元数据/模板/排除保留：**{summary['non_default_records']}**")
    lines.append("- 文档 embedding 使用原始 `text_for_embedding`；查询 embedding 使用 Qwen3 查询 instruction 前缀。")
    lines.append("- 物理上先做一个本地索引，检索时用 `namespace` 分机房过滤；`corpus_layer` 控制事实、证据、模板和排除层。\n")

    lines.append("## Corpus Layer\n")
    lines.append("| layer | count |")
    lines.append("|---|---:|")
    for layer, count in sorted(summary["counts_by_corpus_layer"].items()):
        lines.append(f"| `{layer}` | {count} |")

    lines.append("\n## 默认索引分库\n")
    lines.append("| namespace | default_index_count |")
    lines.append("|---|---:|")
    for namespace, count in sorted(summary["default_counts_by_namespace"].items()):
        lines.append(f"| `{namespace}` | {count} |")

    lines.append("\n## Embedding Policy\n")
    lines.append("| policy | count |")
    lines.append("|---|---:|")
    for policy, count in sorted(summary["counts_by_embedding_policy"].items()):
        lines.append(f"| `{policy}` | {count} |")

    lines.append("\n## 默认入库样例\n")
    lines.append("| namespace | layer | source_type | policy | anchor | text_for_embedding |")
    lines.append("|---|---|---|---|---|---|")
    for record in default_records[:20]:
        lines.append(
            f"| `{record['namespace']}` | `{record['corpus_layer']}` | `{record['source_type']}` | "
            f"`{record['embedding_policy']}` | {markdown_text(record.get('anchor'), 42)} | "
            f"{markdown_text(record.get('text_for_embedding'), 160)} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def smoke_priority(record: dict[str, Any]) -> int:
    text = f"{record.get('text_for_embedding') or ''} {record.get('raw_text') or ''}"
    return sum(1 for keyword in SMOKE_KEYWORDS if keyword in text)


def has_any_keyword(record: dict[str, Any], keywords: list[str]) -> bool:
    text = f"{record.get('text_for_embedding') or ''} {record.get('raw_text') or ''}"
    return any(keyword in text for keyword in keywords)


def select_records(records: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    default_records = [record for record in records if record.get("default_index")]
    if limit is None or limit <= 0 or limit >= len(default_records):
        return default_records

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    # A small smoke-test index must contain each query theme; otherwise a valid
    # embedding/rerank pipeline can look bad simply because the topic was absent.
    group_budget = max(3, limit // (len(SMOKE_KEYWORD_GROUPS) * 3))
    for _group_name, keywords in SMOKE_KEYWORD_GROUPS.items():
        group_records = sorted(
            [record for record in default_records if has_any_keyword(record, keywords)],
            key=lambda record: (-smoke_priority(record), record["namespace"], record["chunk_id"]),
        )
        for record in group_records[:group_budget]:
            if record["chunk_id"] in selected_ids:
                continue
            selected.append(record)
            selected_ids.add(record["chunk_id"])
            if len(selected) >= limit:
                return selected

    priority_records = sorted(
        [record for record in default_records if smoke_priority(record) > 0 and record["chunk_id"] not in selected_ids],
        key=lambda record: (-smoke_priority(record), record["namespace"], record["chunk_id"]),
    )
    priority_budget = max(0, min(len(priority_records), limit // 2 - len(selected)))
    for record in priority_records[:priority_budget]:
        selected.append(record)
        selected_ids.add(record["chunk_id"])

    by_namespace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in default_records:
        if record["chunk_id"] not in selected_ids:
            by_namespace[record["namespace"]].append(record)

    while len(selected) < limit and by_namespace:
        progressed = False
        for namespace in sorted(list(by_namespace)):
            bucket = by_namespace[namespace]
            if not bucket:
                del by_namespace[namespace]
                continue
            selected.append(bucket.pop(0))
            selected_ids.add(selected[-1]["chunk_id"])
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break

    return selected


def batch_records(records: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]


def build_index(
    manifest_path: Path,
    out_dir: Path = DEFAULT_OUT_DIR,
    limit: int | None = None,
    batch_size: int = 16,
    endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    all_records = read_jsonl(manifest_path)
    records = select_records(all_records, limit=limit)
    if not records:
        raise RuntimeError("no records selected for embedding")

    client = EmbeddingClient(endpoint=endpoint, model=model)
    vectors = array("f")
    dimension: int | None = None

    started = time.time()
    for batch_no, batch in enumerate(batch_records(records, batch_size), 1):
        texts = [record["text_for_embedding"] for record in batch]
        embeddings = client.embed(texts)
        for vector in embeddings:
            if dimension is None:
                dimension = len(vector)
            if len(vector) != dimension:
                raise RuntimeError(f"embedding dimension changed from {dimension} to {len(vector)}")
            vectors.extend(vector)
        print(f"embedded batch {batch_no}: {len(batch)} records")

    assert dimension is not None
    embeddings_path = out_dir / "index_embeddings.f32"
    with embeddings_path.open("wb") as f:
        vectors.tofile(f)

    index_records_path = out_dir / "index_records.jsonl"
    write_jsonl(index_records_path, records)

    meta = {
        "record_count": len(records),
        "dimension": dimension,
        "embedding_model": model,
        "embedding_endpoint": endpoint,
        "manifest_path": str(manifest_path),
        "index_records_path": str(index_records_path),
        "embeddings_path": str(embeddings_path),
        "limit": limit,
        "batch_size": batch_size,
        "elapsed_seconds": round(time.time() - started, 3),
        "counts_by_namespace": dict(Counter(record["namespace"] for record in records)),
        "counts_by_corpus_layer": dict(Counter(record["corpus_layer"] for record in records)),
        "counts_by_source_type": dict(Counter(record["source_type"] for record in records)),
    }
    write_json(out_dir / "index_meta.json", meta)
    return meta


def load_index(out_dir: Path = DEFAULT_OUT_DIR) -> tuple[dict[str, Any], list[dict[str, Any]], array]:
    meta = json.loads((out_dir / "index_meta.json").read_text(encoding="utf-8"))
    records = read_jsonl(Path(meta["index_records_path"]))
    vectors = array("f")
    with Path(meta["embeddings_path"]).open("rb") as f:
        vectors.fromfile(f, meta["record_count"] * meta["dimension"])
    if len(records) != meta["record_count"]:
        raise RuntimeError("index record count mismatch")
    return meta, records, vectors


def cosine_scores(
    query_vector: list[float],
    records: list[dict[str, Any]],
    vectors: array,
    dimension: int,
    namespaces: set[str] | None = None,
    layers: set[str] | None = None,
) -> list[tuple[float, int]]:
    q_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
    results: list[tuple[float, int]] = []
    for index, record in enumerate(records):
        if namespaces and record.get("namespace") not in namespaces:
            continue
        if layers and record.get("corpus_layer") not in layers:
            continue
        start = index * dimension
        dot = 0.0
        d_norm_sq = 0.0
        for offset, q_value in enumerate(query_vector):
            value = vectors[start + offset]
            dot += q_value * value
            d_norm_sq += value * value
        score = dot / (q_norm * (math.sqrt(d_norm_sq) or 1.0))
        score *= float(record.get("rank_boost") or 1.0)
        results.append((score, index))
    results.sort(key=lambda item: item[0], reverse=True)
    return results


def search_index(
    query: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    top_k: int = 12,
    namespaces: list[str] | None = None,
    layers: list[str] | None = None,
    endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[dict[str, Any]]:
    meta, records, vectors = load_index(out_dir)
    client = EmbeddingClient(endpoint=endpoint, model=model)
    query_vector = client.embed_query(query)
    namespace_set = set(namespaces) if namespaces else None
    layer_set = set(layers) if layers else {"fact", "evidence"}
    scored = cosine_scores(
        query_vector,
        records,
        vectors,
        int(meta["dimension"]),
        namespaces=namespace_set,
        layers=layer_set,
    )
    hits: list[dict[str, Any]] = []
    for score, index in scored[:top_k]:
        record = records[index]
        hits.append(
            {
                "vector_rank": len(hits) + 1,
                "vector_score": round(score, 6),
                "chunk_id": record["chunk_id"],
                "namespace": record["namespace"],
                "corpus_layer": record["corpus_layer"],
                "source_type": record["source_type"],
                "embedding_policy": record["embedding_policy"],
                "anchor": record.get("anchor"),
                "file_name": record.get("file_name"),
                "raw_text": record.get("raw_text"),
                "text_for_embedding": record.get("text_for_embedding"),
                "proof_attachment_ids": record.get("proof_attachment_ids") or [],
                "source": record.get("source") or {},
            }
        )
    return hits


def retrieval_smoke_test(
    out_dir: Path = DEFAULT_OUT_DIR,
    rerank_endpoint: str = DEFAULT_RERANK_ENDPOINT,
    embedding_endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    model: str = DEFAULT_EMBEDDING_MODEL,
    rerank_model: str = DEFAULT_RERANK_MODEL,
) -> dict[str, Any]:
    reranker = RerankClient(endpoint=rerank_endpoint, model=rerank_model)
    report: list[dict[str, Any]] = []
    for item in SMOKE_QUERIES:
        query = item["query"]
        hits = search_index(
            query,
            out_dir=out_dir,
            top_k=12,
            namespaces=item.get("namespaces"),
            endpoint=embedding_endpoint,
            model=model,
        )
        reranked = reranker.rerank(query, [hit["text_for_embedding"] for hit in hits], top_n=5)
        reranked_hits: list[dict[str, Any]] = []
        for rank, rerank_item in enumerate(reranked, 1):
            original_index = int(rerank_item.get("index", 0))
            if original_index < 0 or original_index >= len(hits):
                continue
            hit = dict(hits[original_index])
            hit["rerank_rank"] = rank
            hit["rerank_score"] = rerank_item.get("relevance_score")
            reranked_hits.append(hit)
        report.append(
            {
                "query": query,
                "namespaces": item.get("namespaces") or [],
                "vector_hits": hits[:5],
                "reranked_hits": reranked_hits,
            }
        )

    output = {
        "query_count": len(report),
        "embedding_endpoint": embedding_endpoint,
        "rerank_endpoint": rerank_endpoint,
        "results": report,
    }
    write_json(out_dir / "retrieval_smoke.json", output)
    write_retrieval_markdown(out_dir / "retrieval_smoke.md", output)
    return output


def write_retrieval_markdown(path: Path, output: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 11 检索冒烟测试\n")
    lines.append(f"- 查询数：**{output['query_count']}**")
    lines.append(f"- Embedding endpoint：`{output['embedding_endpoint']}`")
    lines.append(f"- Rerank endpoint：`{output['rerank_endpoint']}`\n")

    for result in output["results"]:
        lines.append(f"## {result['query']}\n")
        lines.append(f"- namespace filter：`{', '.join(result['namespaces'])}`\n")
        lines.append("| rerank | score | namespace | layer | source | anchor | raw_text |")
        lines.append("|---:|---:|---|---|---|---|---|")
        for hit in result["reranked_hits"]:
            score = hit.get("rerank_score")
            score_text = f"{score:.6f}" if isinstance(score, (int, float)) else ""
            lines.append(
                f"| {hit['rerank_rank']} | {score_text} | `{hit['namespace']}` | `{hit['corpus_layer']}` | "
                f"`{hit['source_type']}` | {markdown_text(hit.get('anchor'), 42)} | "
                f"{markdown_text(hit.get('raw_text'), 180)} |"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def run_all(
    out_dir: Path = DEFAULT_OUT_DIR,
    limit: int | None = 160,
    batch_size: int = 16,
    embedding_endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    rerank_endpoint: str = DEFAULT_RERANK_ENDPOINT,
    rerank_model: str = DEFAULT_RERANK_MODEL,
    segments_path: Path = STEP05_SEGMENTS,
    resolved_segments_path: Path = STEP09_SEGMENTS,
    semantic_audit_path: Path = STEP10_AUDIT,
) -> dict[str, Any]:
    manifest_summary = build_manifest(
        out_dir,
        segments_path=segments_path,
        resolved_segments_path=resolved_segments_path,
        semantic_audit_path=semantic_audit_path,
    )
    index_meta = build_index(
        out_dir / "ingestion_manifest.jsonl",
        out_dir=out_dir,
        limit=limit,
        batch_size=batch_size,
        endpoint=embedding_endpoint,
        model=embedding_model,
    )
    retrieval = retrieval_smoke_test(
        out_dir=out_dir,
        rerank_endpoint=rerank_endpoint,
        embedding_endpoint=embedding_endpoint,
        model=embedding_model,
        rerank_model=rerank_model,
    )
    final_summary = {
        "manifest": manifest_summary,
        "index": index_meta,
        "retrieval_query_count": retrieval["query_count"],
    }
    write_json(out_dir / "summary.json", final_summary)
    return final_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 11: build RAG ingestion manifest, local embedding index, and smoke retrieval report.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", type=Path, default=None)

    manifest_parser = subparsers.add_parser("manifest", parents=[config_parent])
    manifest_parser.add_argument("--out-dir", type=Path, default=None)

    index_parser = subparsers.add_parser("index", parents=[config_parent])
    index_parser.add_argument("--manifest", type=Path, default=None)
    index_parser.add_argument("--out-dir", type=Path, default=None)
    index_parser.add_argument("--limit", type=int, default=160, help="0 or negative means full default index.")
    index_parser.add_argument("--batch-size", type=int, default=16)
    index_parser.add_argument("--endpoint", default=None)
    index_parser.add_argument("--model", default=None)

    smoke_parser = subparsers.add_parser("smoke", parents=[config_parent])
    smoke_parser.add_argument("--out-dir", type=Path, default=None)
    smoke_parser.add_argument("--embedding-endpoint", default=None)
    smoke_parser.add_argument("--embedding-model", default=None)
    smoke_parser.add_argument("--rerank-endpoint", default=None)
    smoke_parser.add_argument("--rerank-model", default=None)

    run_parser = subparsers.add_parser("run", parents=[config_parent])
    run_parser.add_argument("--out-dir", type=Path, default=None)
    run_parser.add_argument("--limit", type=int, default=160, help="0 or negative means full default index.")
    run_parser.add_argument("--batch-size", type=int, default=16)
    run_parser.add_argument("--embedding-endpoint", default=None)
    run_parser.add_argument("--embedding-model", default=None)
    run_parser.add_argument("--rerank-endpoint", default=None)
    run_parser.add_argument("--rerank-model", default=None)

    args = parser.parse_args()
    config = load_app_config(args.config)
    step11_out_dir = config.paths.artifacts_dir / "11_embedding_build"
    segments_path = config.paths.artifacts_dir / "05_segment_extract/segments.jsonl"
    resolved_segments_path = config.paths.artifacts_dir / "09_table_candidate_resolution/resolved_table_segments.jsonl"
    semantic_audit_path = config.paths.artifacts_dir / "10_semantic_segment_audit/semantic_audit.jsonl"
    if args.command == "manifest":
        out_dir = args.out_dir or step11_out_dir
        summary = build_manifest(
            out_dir,
            segments_path=segments_path,
            resolved_segments_path=resolved_segments_path,
            semantic_audit_path=semantic_audit_path,
        )
        print(f"built ingestion manifest: {summary['total_records']} records -> {out_dir}")
    elif args.command == "index":
        limit = None if args.limit <= 0 else args.limit
        out_dir = args.out_dir or step11_out_dir
        manifest_path = args.manifest or (out_dir / "ingestion_manifest.jsonl")
        meta = build_index(
            manifest_path,
            out_dir,
            limit,
            args.batch_size,
            args.endpoint or config.services.embedding_endpoint,
            args.model or config.services.embedding_model,
        )
        print(f"built embedding index: {meta['record_count']} records, dim={meta['dimension']} -> {out_dir}")
    elif args.command == "smoke":
        out_dir = args.out_dir or step11_out_dir
        output = retrieval_smoke_test(
            out_dir,
            args.rerank_endpoint or config.services.rerank_endpoint,
            args.embedding_endpoint or config.services.embedding_endpoint,
            args.embedding_model or config.services.embedding_model,
            args.rerank_model or config.services.rerank_model,
        )
        print(f"ran retrieval smoke test: {output['query_count']} queries -> {out_dir}")
    elif args.command == "run":
        limit = None if args.limit <= 0 else args.limit
        out_dir = args.out_dir or step11_out_dir
        summary = run_all(
            out_dir=out_dir,
            limit=limit,
            batch_size=args.batch_size,
            embedding_endpoint=args.embedding_endpoint or config.services.embedding_endpoint,
            embedding_model=args.embedding_model or config.services.embedding_model,
            rerank_endpoint=args.rerank_endpoint or config.services.rerank_endpoint,
            rerank_model=args.rerank_model or config.services.rerank_model,
            segments_path=segments_path,
            resolved_segments_path=resolved_segments_path,
            semantic_audit_path=semantic_audit_path,
        )
        print(
            "step 11 complete: "
            f"manifest={summary['manifest']['total_records']} records, "
            f"index={summary['index']['record_count']} records"
        )


if __name__ == "__main__":
    main()
