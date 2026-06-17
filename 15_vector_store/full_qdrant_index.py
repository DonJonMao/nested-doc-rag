from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

try:
    from nested_doc_rag.embedding import DEFAULT_EMBEDDING_ENDPOINT, DEFAULT_EMBEDDING_MODEL, QUERY_INSTRUCTION, EmbeddingClient
    from nested_doc_rag.retrieval.lexical import BM25Index
except ModuleNotFoundError:
    import site

    site.addsitedir(str(Path(__file__).resolve().parents[1]))
    from nested_doc_rag.embedding import DEFAULT_EMBEDDING_ENDPOINT, DEFAULT_EMBEDDING_MODEL, QUERY_INSTRUCTION, EmbeddingClient
    from nested_doc_rag.retrieval.lexical import BM25Index


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STEP01_FILES = PROJECT_ROOT / "artifacts/01_file_registration/file_manifest.jsonl"
STEP02_ROUTED = PROJECT_ROOT / "artifacts/02_datacenter_routing/routed_manifest.jsonl"
STEP04A_REPORT = PROJECT_ROOT / "artifacts/04a_structure_parse/parse_report.json"
STEP04B_SEGMENTS = PROJECT_ROOT / "artifacts/04b_embedded_object_parse/embedded_segments.jsonl"
STEP11_MANIFEST = PROJECT_ROOT / "artifacts/11_embedding_build/ingestion_manifest.jsonl"
DEFAULT_OUT_DIR = PROJECT_ROOT / "artifacts/15_vector_store"
DEFAULT_COLLECTION = "datacenter_chunks_v1"
MAX_EMBEDDING_TEXT_CHARS = 3500


EXCLUDED_EMBEDDED_FILE_TYPES = {"dwg"}
EXCLUDED_DOCUMENT_ROLES = {"survey_form"}
DEFAULT_QUERY_LAYERS = {"fact", "evidence", "raw_text", "intro_doc", "meta"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def stable_id(*parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def point_id(chunk_id: str) -> str:
    return str(uuid.UUID(hashlib.sha256(chunk_id.encode("utf-8")).hexdigest()[:32]))


def compact_text(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split())
    if limit and len(text) > limit:
        return text[: limit - 1] + "..."
    return text


def embedding_input_text(value: Any) -> str:
    text = compact_text(value)
    if len(text) <= MAX_EMBEDDING_TEXT_CHARS:
        return text
    return text[:MAX_EMBEDDING_TEXT_CHARS]


def md(value: Any, limit: int = 120) -> str:
    return compact_text(value, limit).replace("|", "\\|")


def safe_payload(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [safe_payload(item) for item in value if safe_payload(item) is not None]
    if isinstance(value, dict):
        return {str(key): safe_payload(item) for key, item in value.items() if safe_payload(item) is not None}
    return str(value)


def file_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    files = {record["file_id"]: record for record in read_jsonl(STEP01_FILES)}
    routed = {record["file_id"]: record for record in read_jsonl(STEP02_ROUTED)}
    return files, routed


def namespace_for_intro(file_record: dict[str, Any], routed_record: dict[str, Any] | None) -> tuple[str, list[str]]:
    namespace = file_record.get("data_center_id") or (routed_record or {}).get("data_center_id")
    candidates = [
        candidate.get("data_center_id")
        for candidate in (routed_record or {}).get("route_candidates", [])
        if candidate.get("data_center_id")
    ]
    if namespace:
        return str(namespace), candidates
    # 泛西咸说明文档属于多个楼，放 global，查询 xixian_N + global 时可召回。
    return "global", candidates


def make_base_manifest_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for record in read_jsonl(STEP11_MANIFEST):
        text = compact_text(record.get("text_for_embedding"))
        if not text:
            continue
        copied = dict(record)
        copied["full_store_source"] = "step11_curated_manifest"
        copied["full_store_indexed"] = True
        copied["manifest_default_index"] = bool(record.get("default_index"))
        # 第 15 步是“除图片/DWG/工勘单外全部入库”，所以第 11 步中
        # metadata/template/excluded 也写入 Qdrant，但保留原 corpus_layer。
        records.append(copied)
    return records


def table_rows_from_cells(table: dict[str, Any]) -> list[tuple[int, list[str]]]:
    rows: dict[int, dict[int, str]] = {}
    for cell in table.get("cells") or []:
        row_index = int(cell.get("row_index") or 0)
        col_index = int(cell.get("col_index") or 0)
        if not row_index or not col_index:
            continue
        rows.setdefault(row_index, {})[col_index] = compact_text(cell.get("text"))
    if not rows and table.get("rows_sample"):
        return [(index, [compact_text(value) for value in row]) for index, row in enumerate(table["rows_sample"], 1)]
    output: list[tuple[int, list[str]]] = []
    for row_index in sorted(rows):
        cols = rows[row_index]
        max_col = max(cols) if cols else 0
        output.append((row_index, [cols.get(col, "") for col in range(1, max_col + 1)]))
    return output


def row_to_text(row_index: int, row_values: list[str], header: list[str] | None) -> str:
    values = [compact_text(value) for value in row_values]
    if header and row_index != 1:
        pairs: list[str] = []
        for index, value in enumerate(values):
            if not value:
                continue
            label = compact_text(header[index]) if index < len(header) else f"列{index + 1}"
            pairs.append(f"{label}：{value}" if label else value)
        return "；".join(pairs)
    return "；".join(value for value in values if value)


def make_intro_doc_records() -> list[dict[str, Any]]:
    _, routed_by_file_id = file_maps()
    report = read_json(STEP04A_REPORT)
    records: list[dict[str, Any]] = []
    for file_record in report.get("files") or []:
        if file_record.get("document_role") != "intro_doc":
            continue
        routed_record = routed_by_file_id.get(file_record["file_id"])
        namespace, namespace_candidates = namespace_for_intro(file_record, routed_record)
        file_name = file_record.get("file_name")
        for para in file_record.get("paragraphs") or []:
            raw_text = compact_text(para.get("text"))
            if not raw_text:
                continue
            block_index = para.get("block_index")
            chunk_id = "intro_" + stable_id(file_record.get("file_id"), "paragraph", block_index, raw_text)
            anchor = f"{file_name}!paragraph {block_index}"
            text_for_embedding = (
                f"数据中心：{namespace}。文件：{file_name}。文档类型：机房情况说明介绍。"
                f"位置：段落{block_index}。内容：{raw_text}。"
            )
            records.append(
                {
                    "chunk_id": chunk_id,
                    "source_type": "intro_doc_paragraph",
                    "source_segment_id": chunk_id,
                    "namespace": namespace,
                    "namespace_candidates": namespace_candidates,
                    "data_center_id": namespace,
                    "corpus_layer": "intro_doc",
                    "embedding_policy": "embed_intro",
                    "default_index": True,
                    "full_store_source": "step04a_intro_doc",
                    "full_store_indexed": True,
                    "rank_boost": 1.0,
                    "text_for_embedding": text_for_embedding,
                    "raw_text": raw_text,
                    "file_name": file_name,
                    "relative_path": file_record.get("relative_path"),
                    "sheet_name": None,
                    "row_index": block_index,
                    "anchor": anchor,
                    "table_id": None,
                    "parent_chunk_id": None,
                    "parent_attachment_id": None,
                    "embedded_file_name": None,
                    "proof_attachment_ids": [],
                    "proof_attachment_count": 0,
                    "proof_cell_refs": [],
                    "semantic_flags": [],
                    "retrieval_tags": ["intro_doc", namespace, file_name],
                    "source": {
                        "file_id": file_record.get("file_id"),
                        "file_name": file_name,
                        "block_type": "paragraph",
                        "block_index": block_index,
                        "route_status": (routed_record or {}).get("route_status"),
                        "namespace_candidates": namespace_candidates,
                    },
                }
            )

        for table in file_record.get("tables") or []:
            table_index = table.get("table_index")
            rows = table_rows_from_cells(table)
            header = rows[0][1] if rows else None
            for row_index, row_values in rows:
                raw_text = compact_text(row_to_text(row_index, row_values, header))
                if not raw_text:
                    continue
                chunk_id = "intro_" + stable_id(file_record.get("file_id"), "table", table_index, row_index, raw_text)
                anchor = f"{file_name}!table {table_index} row {row_index}"
                text_for_embedding = (
                    f"数据中心：{namespace}。文件：{file_name}。文档类型：机房情况说明介绍。"
                    f"位置：表{table_index} 行{row_index}。内容：{raw_text}。"
                )
                records.append(
                    {
                        "chunk_id": chunk_id,
                        "source_type": "intro_doc_table_row",
                        "source_segment_id": chunk_id,
                        "namespace": namespace,
                        "namespace_candidates": namespace_candidates,
                        "data_center_id": namespace,
                        "corpus_layer": "intro_doc",
                        "embedding_policy": "embed_intro",
                        "default_index": True,
                        "full_store_source": "step04a_intro_doc",
                        "full_store_indexed": True,
                        "rank_boost": 1.0,
                        "text_for_embedding": text_for_embedding,
                        "raw_text": raw_text,
                        "file_name": file_name,
                        "relative_path": file_record.get("relative_path"),
                        "sheet_name": None,
                        "row_index": row_index,
                        "anchor": anchor,
                        "table_id": f"intro_table_{file_record.get('file_id')}_{table_index}",
                        "parent_chunk_id": None,
                        "parent_attachment_id": None,
                        "embedded_file_name": None,
                        "proof_attachment_ids": [],
                        "proof_attachment_count": 0,
                        "proof_cell_refs": [],
                        "semantic_flags": [],
                        "retrieval_tags": ["intro_doc", "intro_doc_table", namespace, file_name],
                        "source": {
                            "file_id": file_record.get("file_id"),
                            "file_name": file_name,
                            "block_type": "table_row",
                            "table_index": table_index,
                            "row_index": row_index,
                            "route_status": (routed_record or {}).get("route_status"),
                            "namespace_candidates": namespace_candidates,
                        },
                    }
                )
    return records


def make_embedded_raw_records() -> list[dict[str, Any]]:
    files_by_id, _ = file_maps()
    records: list[dict[str, Any]] = []
    for segment in read_jsonl(STEP04B_SEGMENTS):
        parent_file_id = segment.get("parent_file_id")
        parent_file = files_by_id.get(parent_file_id, {})
        if parent_file.get("document_role") in EXCLUDED_DOCUMENT_ROLES:
            continue
        embedded_type = compact_text(segment.get("embedded_file_type")).lower()
        if embedded_type in EXCLUDED_EMBEDDED_FILE_TYPES:
            continue
        if "image" in compact_text(segment.get("segment_type")).lower():
            continue
        raw_text = compact_text(segment.get("raw_text"))
        text_for_embedding = compact_text(segment.get("embedding_text") or raw_text)
        if not raw_text or not text_for_embedding:
            continue
        namespace = compact_text(segment.get("data_center_id")) or "global"
        local_anchor = segment.get("local_anchor") or {}
        local_part = (
            local_anchor.get("block_type")
            or local_anchor.get("paragraph_index")
            or local_anchor.get("row_index")
            or local_anchor.get("page_index")
            or ""
        )
        chunk_id = "raw_" + stable_id("embedded_raw", segment.get("segment_id"), text_for_embedding)
        parent_sheet = segment.get("parent_sheet_name")
        parent_cell = segment.get("parent_source_cell")
        anchor = f"{parent_sheet}!{parent_cell} {embedded_type} {local_part}".strip()
        records.append(
            {
                "chunk_id": chunk_id,
                "source_type": "embedded_raw_segment",
                "source_segment_id": segment.get("segment_id"),
                "namespace": namespace,
                "data_center_id": namespace,
                "corpus_layer": "raw_text",
                "embedding_policy": "embed_raw_full",
                "default_index": True,
                "full_store_source": "step04b_raw_embedded_segments",
                "full_store_indexed": True,
                "rank_boost": 0.88,
                "text_for_embedding": text_for_embedding,
                "raw_text": raw_text,
                "file_name": segment.get("parent_file_name"),
                "relative_path": parent_file.get("relative_path") or segment.get("parent_file_name"),
                "sheet_name": parent_sheet,
                "row_index": parent_cell,
                "anchor": anchor,
                "table_id": None,
                "parent_chunk_id": segment.get("parent_segment_id"),
                "parent_attachment_id": segment.get("parent_attachment_id"),
                "embedded_file_name": segment.get("embedded_file_name"),
                "embedded_file_type": embedded_type,
                "proof_attachment_ids": [],
                "proof_attachment_count": 0,
                "proof_cell_refs": [parent_cell] if parent_cell else [],
                "semantic_flags": [],
                "retrieval_tags": [
                    "embedded_raw",
                    namespace,
                    embedded_type,
                    segment.get("segment_type"),
                    segment.get("embedded_file_name"),
                ],
                "source": {
                    "parent_file_id": parent_file_id,
                    "parent_file_name": segment.get("parent_file_name"),
                    "parent_sheet_name": parent_sheet,
                    "parent_source_cell": parent_cell,
                    "embedded_object_id": segment.get("embedded_object_id"),
                    "embedded_file_name": segment.get("embedded_file_name"),
                    "embedded_file_type": embedded_type,
                    "segment_type": segment.get("segment_type"),
                    "local_anchor": local_anchor,
                    "source_chain": segment.get("source_chain") or [],
                },
            }
        )
    return records


def build_expanded_manifest(out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    records.extend(make_base_manifest_records())
    records.extend(make_intro_doc_records())
    records.extend(make_embedded_raw_records())

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_count = 0
    for record in records:
        chunk_id = record["chunk_id"]
        if chunk_id in seen:
            duplicate_count += 1
            continue
        seen.add(chunk_id)
        deduped.append(record)

    write_jsonl(out_dir / "expanded_ingestion_manifest.jsonl", deduped)
    lexical_index_path = out_dir / "lexical_index.json"
    BM25Index.from_records(deduped).save(lexical_index_path)
    summary = {
        "total_records": len(deduped),
        "duplicate_chunk_ids_skipped": duplicate_count,
        "policy": {
            "include": "第11步清单全部文本、intro_doc 说明文档段落/表格、04B 可抽文本附件原始片段。",
            "exclude": "工勘单、图片 OCR 文本、DWG 图纸。",
            "note": "图片仍通过 proof_attachment_ids 作为证据附件；不做 OCR，不作为文本 chunk。",
        },
        "counts_by_full_store_source": dict(Counter(record.get("full_store_source") for record in deduped)),
        "counts_by_source_type": dict(Counter(record.get("source_type") for record in deduped)),
        "counts_by_corpus_layer": dict(Counter(record.get("corpus_layer") for record in deduped)),
        "counts_by_namespace": dict(Counter(record.get("namespace") for record in deduped)),
        "counts_by_embedding_policy": dict(Counter(record.get("embedding_policy") for record in deduped)),
        "default_query_layers": sorted(DEFAULT_QUERY_LAYERS),
        "lexical_index_path": str(lexical_index_path),
    }
    write_json(out_dir / "manifest_summary.json", summary)
    write_visualization(out_dir / "visualization.md", deduped, summary)
    return summary


def make_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"text_for_embedding"}
    }
    payload["text_for_embedding"] = record.get("text_for_embedding")
    payload["point_id"] = point_id(record["chunk_id"])
    return safe_payload(payload)


def recreate_collection(client: QdrantClient, collection_name: str, dimension: int) -> None:
    if client.collection_exists(collection_name):
        client.delete_collection(collection_name)
    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
    )
    for field in ["namespace", "source_type", "corpus_layer", "file_name", "embedded_file_type"]:
        try:
            client.create_payload_index(collection_name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD)
        except Exception:
            pass


def build_qdrant_index(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    collection_name: str = DEFAULT_COLLECTION,
    batch_size: int = 32,
    embedding_endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    recreate: bool = True,
    resume: bool = False,
    limit: int | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    manifest_path = out_dir / "expanded_ingestion_manifest.jsonl"
    if not manifest_path.exists():
        build_expanded_manifest(out_dir)
    all_records = read_jsonl(manifest_path)
    records = all_records[:limit] if limit and limit > 0 else all_records
    if not records:
        raise RuntimeError("expanded manifest has no records")

    db_path = out_dir / "qdrant"
    if recreate and db_path.exists():
        shutil.rmtree(db_path)
    db_path.mkdir(parents=True, exist_ok=True)

    embedder = EmbeddingClient(endpoint=embedding_endpoint, model=embedding_model, timeout_seconds=240)
    client = QdrantClient(path=str(db_path))
    dimension: int | None = None
    start_index = 0
    collection_exists = client.collection_exists(collection_name)
    if resume and collection_exists:
        try:
            start_index = int(client.count(collection_name=collection_name, exact=True).count)
        except Exception:
            start_index = int(client.get_collection(collection_name).points_count or 0)
        start_index = min(start_index, len(records))
        print(f"resuming qdrant build from existing points: {start_index}")
    total = 0
    started = time.time()

    for batch_no, start in enumerate(range(start_index, len(records), batch_size), 1):
        batch = records[start : start + batch_size]
        texts = [embedding_input_text(record["text_for_embedding"]) for record in batch]
        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                vectors = embedder.embed(texts)
                break
            except Exception as exc:  # remote embedding can reset long-running calls
                last_error = exc
                wait_seconds = min(5 * attempt, 20)
                print(f"embedding batch failed at start={start}, attempt={attempt}/{max_retries}: {exc}")
                if attempt < max_retries:
                    time.sleep(wait_seconds)
        else:
            raise RuntimeError(f"embedding batch failed after {max_retries} attempts at start={start}") from last_error
        if dimension is None:
            dimension = len(vectors[0])
            if recreate or not collection_exists:
                recreate_collection(client, collection_name, dimension)
                collection_exists = True
        points = [
            models.PointStruct(
                id=point_id(record["chunk_id"]),
                vector=vector,
                payload=make_payload(record),
            )
            for record, vector in zip(batch, vectors, strict=False)
        ]
        client.upsert(collection_name=collection_name, points=points)
        total += len(points)
        print(f"qdrant embedded/upserted batch {batch_no}: {len(points)} records, total_new={total}, absolute={start + len(points)}")

    assert dimension is not None
    info = client.get_collection(collection_name)
    summary = {
        "collection_name": collection_name,
        "db_path": str(db_path),
        "record_count": int(info.points_count or total),
        "upserted_this_run": total,
        "start_index": start_index,
        "total_manifest_records": len(records),
        "qdrant_points_count": info.points_count,
        "dimension": dimension,
        "embedding_model": embedding_model,
        "embedding_endpoint": embedding_endpoint,
        "batch_size": batch_size,
        "limit": limit,
        "elapsed_seconds": round(time.time() - started, 3),
        "counts_by_full_store_source": dict(Counter(record.get("full_store_source") for record in records)),
        "counts_by_source_type": dict(Counter(record.get("source_type") for record in records)),
        "counts_by_corpus_layer": dict(Counter(record.get("corpus_layer") for record in records)),
        "counts_by_namespace": dict(Counter(record.get("namespace") for record in records)),
        "query_instruction": QUERY_INSTRUCTION,
        "embedding_text_char_limit": MAX_EMBEDDING_TEXT_CHARS,
        "truncated_embedding_inputs": sum(
            1 for record in records if len(compact_text(record.get("text_for_embedding"))) > MAX_EMBEDDING_TEXT_CHARS
        ),
    }
    write_json(out_dir / "qdrant_build_summary.json", summary)
    return summary


def qdrant_filter(namespaces: list[str] | None, layers: list[str] | None) -> models.Filter | None:
    must: list[models.FieldCondition] = []
    if namespaces:
        must.append(models.FieldCondition(key="namespace", match=models.MatchAny(any=namespaces)))
    if layers:
        must.append(models.FieldCondition(key="corpus_layer", match=models.MatchAny(any=layers)))
    return models.Filter(must=must) if must else None


def search_qdrant(
    query: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    collection_name: str = DEFAULT_COLLECTION,
    namespaces: list[str] | None = None,
    layers: list[str] | None = None,
    top_k: int = 8,
    embedding_endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[dict[str, Any]]:
    build_summary = read_json(out_dir / "qdrant_build_summary.json")
    client = QdrantClient(path=build_summary["db_path"])
    vector = EmbeddingClient(endpoint=embedding_endpoint, model=embedding_model).embed_query(query)
    response = client.query_points(
        collection_name=collection_name,
        query=vector,
        query_filter=qdrant_filter(namespaces, layers or sorted(DEFAULT_QUERY_LAYERS)),
        limit=top_k,
        with_payload=True,
    )
    hits: list[dict[str, Any]] = []
    for rank, point in enumerate(response.points, 1):
        payload = point.payload or {}
        hits.append(
            {
                "rank": rank,
                "score": point.score,
                "chunk_id": payload.get("chunk_id"),
                "namespace": payload.get("namespace"),
                "corpus_layer": payload.get("corpus_layer"),
                "source_type": payload.get("source_type"),
                "file_name": payload.get("file_name"),
                "anchor": payload.get("anchor"),
                "raw_text": payload.get("raw_text"),
                "text_for_embedding": payload.get("text_for_embedding"),
                "proof_attachment_ids": payload.get("proof_attachment_ids") or [],
            }
        )
    return hits


def run_smoke_queries(out_dir: Path = DEFAULT_OUT_DIR, collection_name: str = DEFAULT_COLLECTION) -> dict[str, Any]:
    queries = [
        {
            "name": "xixian_4_room_name",
            "query": "西咸4号楼301机房名称和带宽信息",
            "namespaces": ["xixian_4", "global"],
        },
        {
            "name": "xixian_4_power",
            "query": "西咸4号楼市电接入和柴发备用电源容量",
            "namespaces": ["xixian_4", "global"],
        },
        {
            "name": "global_4a",
            "query": "零信任登录4A步骤",
            "namespaces": ["global", "xixian_4", "xixian_6", "xianyang"],
        },
    ]
    results = []
    for item in queries:
        hits = search_qdrant(
            item["query"],
            out_dir=out_dir,
            collection_name=collection_name,
            namespaces=item["namespaces"],
            top_k=8,
        )
        results.append({**item, "hits": hits})
    output = {"query_count": len(results), "results": results}
    write_json(out_dir / "qdrant_smoke_results.json", output)
    write_smoke_markdown(out_dir / "qdrant_smoke_results.md", output)
    return output


def write_smoke_markdown(path: Path, output: dict[str, Any]) -> None:
    lines = ["# Step 15 Qdrant 全量库检索抽查\n"]
    for result in output["results"]:
        lines.append(f"## {result['query']}\n")
        lines.append(f"- namespaces: `{', '.join(result['namespaces'])}`\n")
        lines.append("| rank | score | namespace | layer | source | anchor | raw_text |")
        lines.append("|---:|---:|---|---|---|---|---|")
        for hit in result["hits"]:
            lines.append(
                f"| {hit['rank']} | {hit['score']:.6f} | `{hit['namespace']}` | `{hit['corpus_layer']}` | "
                f"`{hit['source_type']}` | {md(hit.get('anchor'), 48)} | {md(hit.get('raw_text'), 180)} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_visualization(path: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 15 全量 Qdrant 向量库\n")
    lines.append("## 入库原则\n")
    lines.append("- 入库：第 11 步清单全部文本、说明文档 `intro_doc`、04B 可抽文本附件原始片段。")
    lines.append("- 排除：工勘单、图片 OCR 文本、DWG 图纸。")
    lines.append("- 图片仍作为 `proof_attachment_ids` 证据附件挂在文本 chunk 后，不做 OCR。")
    lines.append("- 一个 collection：`datacenter_chunks_v1`；用 `namespace` 做机房逻辑分库。\n")
    lines.append("## 清单统计\n")
    lines.append(f"- 扩展清单总数：**{summary['total_records']}**")
    lines.append(f"- 重复 chunk_id 跳过：**{summary['duplicate_chunk_ids_skipped']}**\n")
    lines.append("### 来源层\n")
    lines.append("| source | count |")
    lines.append("|---|---:|")
    for key, count in sorted(summary["counts_by_full_store_source"].items()):
        lines.append(f"| `{key}` | {count} |")
    lines.append("\n### Source Type\n")
    lines.append("| source_type | count |")
    lines.append("|---|---:|")
    for key, count in sorted(summary["counts_by_source_type"].items()):
        lines.append(f"| `{key}` | {count} |")
    lines.append("\n### Corpus Layer\n")
    lines.append("| layer | count |")
    lines.append("|---|---:|")
    for key, count in sorted(summary["counts_by_corpus_layer"].items()):
        lines.append(f"| `{key}` | {count} |")
    lines.append("\n### Namespace\n")
    lines.append("| namespace | count |")
    lines.append("|---|---:|")
    for key, count in sorted(summary["counts_by_namespace"].items()):
        lines.append(f"| `{key}` | {count} |")
    lines.append("\n## 样例\n")
    lines.append("| namespace | layer | source_type | anchor | raw_text |")
    lines.append("|---|---|---|---|---|")
    for record in records[:25]:
        lines.append(
            f"| `{record.get('namespace')}` | `{record.get('corpus_layer')}` | `{record.get('source_type')}` | "
            f"{md(record.get('anchor'), 46)} | {md(record.get('raw_text'), 160)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 15: build expanded full-text Qdrant vector store.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    build_parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    build_parser.add_argument("--batch-size", type=int, default=32)
    build_parser.add_argument("--embedding-endpoint", default=DEFAULT_EMBEDDING_ENDPOINT)
    build_parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    build_parser.add_argument("--limit", type=int, default=0, help="0 means full expanded manifest.")
    build_parser.add_argument("--no-recreate", action="store_true")
    build_parser.add_argument("--resume", action="store_true")
    build_parser.add_argument("--max-retries", type=int, default=3)

    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    smoke_parser.add_argument("--collection", default=DEFAULT_COLLECTION)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    search_parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    search_parser.add_argument("--namespaces", default="")
    search_parser.add_argument("--layers", default="")
    search_parser.add_argument("--top-k", type=int, default=8)

    args = parser.parse_args()
    if args.command == "manifest":
        summary = build_expanded_manifest(args.out_dir)
        print(f"built expanded manifest: {summary['total_records']} records -> {args.out_dir}")
    elif args.command == "build":
        summary = build_qdrant_index(
            out_dir=args.out_dir,
            collection_name=args.collection,
            batch_size=args.batch_size,
            embedding_endpoint=args.embedding_endpoint,
            embedding_model=args.embedding_model,
            recreate=not args.no_recreate and not args.resume,
            resume=args.resume,
            limit=None if args.limit <= 0 else args.limit,
            max_retries=args.max_retries,
        )
        print(f"built qdrant collection: {summary['record_count']} points -> {summary['db_path']}")
    elif args.command == "smoke":
        output = run_smoke_queries(args.out_dir, args.collection)
        print(f"ran qdrant smoke queries: {output['query_count']} queries")
    elif args.command == "search":
        namespaces = [part.strip() for part in args.namespaces.split(",") if part.strip()]
        layers = [part.strip() for part in args.layers.split(",") if part.strip()]
        hits = search_qdrant(
            args.query,
            out_dir=args.out_dir,
            collection_name=args.collection,
            namespaces=namespaces or None,
            layers=layers or None,
            top_k=args.top_k,
        )
        print(json.dumps(hits, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
