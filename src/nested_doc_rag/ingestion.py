from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
import warnings
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from qdrant_client import QdrantClient, models

from nested_doc_rag.config import AppConfig
from nested_doc_rag.embedding import EmbeddingClient
from nested_doc_rag.evidence_images import (
    attachment_from_registry,
    materialize_xlsx_dispimg_registry,
    registry_by_sheet_row,
)
from nested_doc_rag.evidence_record import normalize_evidence_record
from nested_doc_rag.io import display_text, write_json, write_jsonl
from nested_doc_rag.retrieval.field_schema import bind_field_schema_families, build_field_schema_records
from nested_doc_rag.retrieval.qdrant_client import build_qdrant_client
from nested_doc_rag.retrieval.version_scope import (
    VERSIONED_STORAGE_CONTRACT,
    IndexScopeError,
    LegacyIndexScopeWarning,
    build_index_scope_filter,
    canonical_uuid,
    validate_hits_in_index_scopes,
    versioned_point_id,
)

SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".docx", ".txt", ".md", ".csv"}
MAX_CHUNK_CHARS = 1800
NATIVE_PARSER_VERSION = "native-office-v2"
EXCEL_FIELD_LABEL_TERMS = (
    "名称", "容量", "数量", "路数", "电压", "型号", "品牌", "功率", "温度", "湿度", "地址", "位置", "状态", "冗余", "压力", "机房",
)


@dataclass(frozen=True)
class IngestionOptions:
    input_dir: Path
    namespace: str
    knowledge_base_id: str
    out_dir: Path
    config: AppConfig
    qdrant_collection: str | None = None
    qdrant_namespace: str | None = None
    batch_size: int = 16
    resume: bool = False
    index_version_id: str | None = None
    input_snapshot_path: Path | None = None
    input_snapshot_hash: str | None = None
    workspace_id: str | None = None


def source_sha256(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("source sha256 must be a hexadecimal string")
    digest = value.removeprefix("sha256:").lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RuntimeError("source sha256 must contain exactly 64 hexadecimal digits")
    return digest


def load_build_input_snapshot(options: IngestionOptions, *, namespace: str, collection_name: str) -> dict[str, Any] | None:
    if options.index_version_id is None:
        if options.input_snapshot_path is not None or options.input_snapshot_hash is not None:
            raise RuntimeError("a frozen build snapshot requires index_version_id")
        warnings.warn("Legacy ingestion upserts unversioned points without deleting a namespace.", LegacyIndexScopeWarning, stacklevel=2)
        return None
    version = canonical_uuid(options.index_version_id, name="index_version_id")
    kb = canonical_uuid(options.knowledge_base_id, name="knowledge_base_id")
    if options.input_snapshot_path is None or options.input_snapshot_hash is None:
        raise RuntimeError("versioned ingestion requires input_snapshot_path and input_snapshot_hash")
    if not isinstance(options.input_snapshot_hash, str) or re.fullmatch(r"[0-9a-f]{64}", options.input_snapshot_hash) is None:
        raise RuntimeError("input_snapshot_hash must be the 64 lowercase hexadecimal digest of the original bytes")
    data = options.input_snapshot_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != options.input_snapshot_hash:
        raise RuntimeError("frozen build input snapshot raw bytes hash mismatch")
    snapshot = json.loads(data)
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "kb-build-input-v1":
        raise RuntimeError("unsupported build input snapshot schema")
    workspace = canonical_uuid(snapshot.get("workspace_id"), name="workspace_id")
    if options.workspace_id is not None and canonical_uuid(options.workspace_id, name="workspace_id") != workspace:
        raise RuntimeError("build input snapshot workspace mismatch")
    if (canonical_uuid(snapshot.get("knowledge_base_id"), name="knowledge_base_id") != kb
            or canonical_uuid(snapshot.get("index_version_id"), name="index_version_id") != version
            or snapshot.get("namespace") != namespace or snapshot.get("collection") != collection_name):
        raise RuntimeError("build input snapshot scope mismatch")
    documents = snapshot.get("documents")
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("build input snapshot must contain documents")
    seen_documents: set[str] = set()
    seen_paths: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise RuntimeError("build input document must be an object")
        doc_id = canonical_uuid(document.get("document_id"), name="document_id")
        file_id = canonical_uuid(document.get("file_id"), name="file_id")
        filename = document.get("filename")
        rel = document.get("relative_path")
        if not isinstance(filename, str) or not filename or filename in {".", ".."} or any(char in filename for char in ("/", "\\", "\x00")):
            raise RuntimeError("build input filename must be a safe path segment")
        if rel != f"{doc_id}/{file_id}/{filename}" or PurePosixPath(rel).as_posix() != rel:
            raise RuntimeError("build input relative_path must be document UUID/file UUID/safe filename")
        if doc_id in seen_documents or rel in seen_paths:
            raise RuntimeError("build input snapshot contains duplicate documents or paths")
        seen_documents.add(doc_id)
        seen_paths.add(rel)
        for key in ("object_key", "document_role"):
            if not isinstance(document.get(key), str) or not document[key].strip():
                raise RuntimeError(f"build input {key} must be nonempty")
        size = document.get("size_bytes")
        if type(size) is not int or size < 0:
            raise RuntimeError("build input size_bytes must be a nonnegative integer")
        if Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES:
            raise RuntimeError(f"unsupported frozen build source: {filename}")
        document["sha256"] = source_sha256(document.get("sha256"))
    return snapshot


def verify_build_sources(input_dir: Path, documents: list[dict[str, Any]]) -> None:
    root = input_dir.resolve()
    if not root.is_dir():
        raise RuntimeError(f"input_dir does not exist: {root}")
    for document in documents:
        relative = document["relative_path"]
        path = root / relative
        if any(part.is_symlink() for part in (path, *path.parents) if part != root and root in part.parents):
            raise RuntimeError(f"frozen build source cannot be a symlink: {relative}")
        if not path.is_file() or root not in path.resolve().parents:
            raise RuntimeError(f"frozen build source is missing or outside input_dir: {relative}")
        if path.stat().st_size != document["size_bytes"]:
            raise RuntimeError(f"frozen build source size mismatch: {relative}")
        with path.open("rb") as source_file:
            digest = hashlib.file_digest(source_file, "sha256").hexdigest()
        if digest != document["sha256"]:
            raise RuntimeError(f"frozen build source hash mismatch: {relative}")


def run_knowledge_ingestion(options: IngestionOptions) -> dict[str, Any]:
    started = time.time()
    out_dir = options.out_dir
    namespace = display_text(options.qdrant_namespace or options.namespace)
    if not namespace:
        raise RuntimeError("namespace is required")
    collection_name = options.qdrant_collection or options.config.qdrant.collection_name
    input_snapshot = load_build_input_snapshot(options, namespace=namespace, collection_name=collection_name)
    if input_snapshot is not None:
        (out_dir / "validation_receipt.json").unlink(missing_ok=True)
        verify_build_sources(options.input_dir, input_snapshot["documents"])
    out_dir.mkdir(parents=True, exist_ok=True)
    records, skipped = build_ingestion_records(
        options.input_dir,
        namespace=namespace,
        knowledge_base_id=options.knowledge_base_id,
        image_output_dir=out_dir / "evidence_images",
        index_version_id=options.index_version_id,
        source_documents=input_snapshot["documents"] if input_snapshot is not None else None,
    )
    if not records:
        raise RuntimeError(f"no supported text records found in {options.input_dir}")
    schema_records = build_field_schema_records(records)

    manifest_path = out_dir / "ingestion_manifest.jsonl"
    registry_rows = proof_attachment_registry_from_records(records)
    write_jsonl(manifest_path, records)
    if schema_records:
        write_jsonl(out_dir / "field_schema_manifest.jsonl", schema_records)
    if registry_rows:
        write_jsonl(out_dir / "proof_attachment_registry.jsonl", registry_rows)
    if skipped:
        write_jsonl(out_dir / "skipped_files.jsonl", skipped)

    client = EmbeddingClient(
        endpoint=options.config.services.embedding_endpoint,
        model=options.config.services.embedding_model,
        timeout_seconds=options.config.services.timeout_seconds,
        purpose="ingestion_embedding",
    )
    qdrant_path = options.config.paths.qdrant_path
    qdrant = build_qdrant_client(
        qdrant_path=qdrant_path,
        qdrant_url=options.config.qdrant.url,
        api_key_env=options.config.qdrant.api_key_env,
        prefer_grpc=options.config.qdrant.prefer_grpc,
        timeout=options.config.qdrant.timeout,
    )
    point_counts: dict[str, int] = {}
    snapshot_bytes: bytes | None = None
    try:
        upserted, dimension = upsert_records(
            qdrant=qdrant,
            collection_name=collection_name,
            records=[*records, *schema_records],
            embedder=client,
            batch_size=options.batch_size,
            namespace=namespace,
            point_counts=point_counts,
            knowledge_base_id=options.knowledge_base_id,
            index_version_id=options.index_version_id,
        )
        validation_receipt = None
        if input_snapshot is not None:
            validation_receipt = validate_candidate_index(
                qdrant=qdrant, collection_name=collection_name, namespace=namespace,
                knowledge_base_id=options.knowledge_base_id, index_version_id=options.index_version_id,
                records=records, schema_records=schema_records, input_snapshot=input_snapshot,
                input_snapshot_hash=options.input_snapshot_hash,
            )
            verify_build_sources(options.input_dir, input_snapshot["documents"])
            snapshot_bytes = options.input_snapshot_path.read_bytes()
            if hashlib.sha256(snapshot_bytes).hexdigest() != options.input_snapshot_hash:
                raise RuntimeError("frozen build input snapshot changed during candidate validation")
    finally:
        qdrant.close()

    summary = build_summary(
        records=records,
        skipped=skipped,
        collection_name=collection_name,
        qdrant_path=qdrant_path,
        qdrant_url=options.config.qdrant.url,
        namespace=namespace,
        embedding_endpoint=options.config.services.embedding_endpoint,
        embedding_model=options.config.services.embedding_model,
        dimension=dimension,
        upserted=point_counts.get("evidence", 0),
        elapsed_seconds=round(time.time() - started, 3),
        schema_records=schema_records,
        schema_upserted=point_counts.get("field_schema", 0),
        total_upserted=upserted,
    )
    write_json(out_dir / "summary.json", summary)
    write_summary_markdown(out_dir / "run_summary.md", summary)
    manifest_artifacts = {
        "ingestion_manifest": "ingestion_manifest.jsonl",
        "summary": "summary.json",
        "run_summary": "run_summary.md",
    }
    if registry_rows:
        manifest_artifacts["proof_attachment_registry"] = "proof_attachment_registry.jsonl"
    if schema_records:
        manifest_artifacts["field_schema_manifest"] = "field_schema_manifest.jsonl"
    if skipped:
        manifest_artifacts["skipped_files"] = "skipped_files.jsonl"
    if validation_receipt is not None:
        (out_dir / "input_snapshot.json").write_bytes(snapshot_bytes)
        manifest_artifacts["input_snapshot"] = "input_snapshot.json"
        write_json(out_dir / "validation_receipt.json", validation_receipt)
        manifest_artifacts["validation_receipt"] = "validation_receipt.json"
        summary["index_version_id"] = validation_receipt["index_version_id"]
        summary["input_snapshot_hash"] = validation_receipt["input_snapshot_hash"]
        summary["validation_state"] = "validated"
        write_json(out_dir / "summary.json", summary)
    manifest = build_run_manifest(
        summary=summary,
        namespace=namespace,
        knowledge_base_id=options.knowledge_base_id,
        artifacts=manifest_artifacts,
    )
    write_json(out_dir / "run_manifest.json", manifest)
    return summary


def build_ingestion_records(
    input_dir: Path,
    *,
    namespace: str,
    knowledge_base_id: str,
    image_output_dir: Path | None = None,
    index_version_id: str | None = None,
    source_documents: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    version = canonical_uuid(index_version_id, name="index_version_id") if index_version_id is not None else None
    input_dir = input_dir.resolve()
    if not input_dir.exists():
        raise RuntimeError(f"input_dir does not exist: {input_dir}")
    documents_by_path = {doc["relative_path"]: doc for doc in source_documents or []}
    files = ([input_dir / path for path in sorted(documents_by_path)] if source_documents is not None
             else sorted(path for path in input_dir.rglob("*") if path.is_file()))
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            skipped.append({"path": relative_path(path, input_dir), "reason": "unsupported_suffix"})
            continue
        try:
            chunks = list(
                extract_file_chunks(
                    path,
                    input_dir,
                    namespace=namespace,
                    knowledge_base_id=knowledge_base_id,
                    image_output_dir=image_output_dir,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive path reports the real parser failure to ops.
            if version is not None:
                raise RuntimeError(f"frozen build source parse failed: {relative_path(path, input_dir)}: {exc}") from exc
            skipped.append({"path": relative_path(path, input_dir), "reason": f"parse_failed: {exc}"})
            continue
        with path.open("rb") as source_file:
            document_hash = "sha256:" + hashlib.file_digest(source_file, "sha256").hexdigest()
        document = documents_by_path.get(relative_path(path, input_dir))
        if document is not None:
            verify_build_sources(input_dir, [document])
        document_id = document["document_id"] if document is not None else "uploaded_doc_" + stable_chunk_id(knowledge_base_id, namespace, relative_path(path, input_dir), document_hash)
        first_record = len(records)
        for chunk in chunks:
            source_text = str(chunk.get("raw_source_text", chunk["text"]))
            if not source_text.strip():
                continue
            # A structured value must stay with its complete native row. Splitting
            # it would attach a full value to a partial quotation source.
            parts = [source_text] if chunk["evidence_kind"] == "structured_field" else split_text(source_text, MAX_CHUNK_CHARS, preserve_whitespace=True)
            for part_no, part in enumerate(parts, 1):
                anchor = chunk["anchor"]
                if len(parts) > 1:
                    anchor = f"{anchor}#part-{part_no}"
                text_hash = hashlib.sha256(part.encode("utf-8")).hexdigest()
                chunk_id = stable_chunk_id(knowledge_base_id, namespace, chunk["relative_path"], anchor, text_hash)
                records.append(
                    normalize_evidence_record({
                        "chunk_id": chunk_id,
                        "point_id": versioned_point_id(version, chunk_id) if version else str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)),
                        **({"index_version_id": version, "index_version": version, "storage_contract": VERSIONED_STORAGE_CONTRACT} if version else {}),
                        **({"file_id": document["file_id"], "document_role": document["document_role"]} if document is not None else {}),
                        "source_type": chunk["source_type"],
                        "evidence_kind": chunk["evidence_kind"],
                        "namespace": namespace,
                        "knowledge_base_id": knowledge_base_id,
                        "document_id": document_id,
                        "source_document_hash": document_hash,
                        "parser_type": chunk.get("parser_type"),
                        "parser_version": NATIVE_PARSER_VERSION,
                        "corpus_layer": "fact",
                        "embedding_policy": "embed",
                        "default_index": True,
                        "rank_boost": 1.0,
                        "text_for_embedding": f"文件：{path.name}。位置：{anchor}。内容：{display_text(part)}",
                        "raw_text": part,
                        "raw_source_text": part,
                        "source_text_hash": "sha256:" + text_hash,
                        "file_name": path.name,
                        "relative_path": chunk["relative_path"],
                        "sheet_name": chunk.get("sheet_name"),
                        "row_index": chunk.get("row_index"),
                        "cell_range": chunk.get("cell_range"),
                        "table_index": chunk.get("table_index"),
                        "paragraph_index": chunk.get("paragraph_index"),
                        "structural_path": chunk.get("structural_path") or [],
                        "field_name": chunk.get("field_name"),
                        "field_value": chunk.get("field_value"),
                        "metadata": chunk.get("metadata") or {},
                        "anchor": anchor,
                        "proof_attachment_ids": chunk.get("proof_attachment_ids") or [],
                        "proof_attachments": chunk.get("proof_attachments") or [],
                        "source": {
                            "knowledge_base_id": knowledge_base_id,
                            "document_id": document_id,
                            "document_hash": document_hash,
                            **({"index_version_id": version, "index_version": version} if version else {}),
                            **({"file_id": document["file_id"], "object_key": document["object_key"]} if document is not None else {}),
                            "relative_path": chunk["relative_path"],
                            "anchor": anchor,
                            "sheet_name": chunk.get("sheet_name"),
                            "row_index": chunk.get("row_index"),
                            "cell_range": chunk.get("cell_range"),
                            "table_index": chunk.get("table_index"),
                            "paragraph_index": chunk.get("paragraph_index"),
                            "heading_path": chunk.get("structural_path") or [],
                            "proof_attachments": chunk.get("proof_attachments") or [],
                        },
                    })
                )
        if version is not None and len(records) == first_record:
            raise RuntimeError(f"frozen build source produced no native evidence: {relative_path(path, input_dir)}")
    return bind_field_schema_families(records), skipped


def extract_file_chunks(
    path: Path,
    root: Path,
    *,
    namespace: str = "",
    knowledge_base_id: str = "",
    image_output_dir: Path | None = None,
) -> Iterable[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        yield from extract_xlsx_chunks(path, root, namespace=namespace, knowledge_base_id=knowledge_base_id, image_output_dir=image_output_dir)
    elif suffix == ".docx":
        yield from extract_docx_chunks(path, root)
    else:
        yield from extract_text_chunks(path, root)


def excel_header_role(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    key = re.sub(r"\s+", "", value).strip("：:").casefold()
    roles = {
        "label": {"字段", "字段名", "项目", "指标", "指标名称", "参数", "参数名称", "配置项", "名称", "field", "field_name", "parameter"},
        "value": {"值", "数值", "答案", "当前值", "当前实际值", "实际值", "实际情况", "实际信息", "现状", "参数值", "字段值", "能力描述", "value", "actualvalue"},
        "category": {"类别", "分类", "子类", "系统", "专业", "category", "group"},
        "unit": {"单位", "计量单位", "unit"},
        "note": {"备注", "说明", "证据", "证明材料", "图片", "附件", "note", "evidence"},
    }
    return next((role for role, names in roles.items() if key in names), None)


def excel_merged_context(sheet: Any) -> dict[str, dict[str, Any]]:
    from openpyxl.utils import get_column_letter

    output: dict[str, dict[str, Any]] = {}
    for merged_range in sheet.merged_cells.ranges:
        master = sheet.cell(merged_range.min_row, merged_range.min_col)
        for row in range(merged_range.min_row, merged_range.max_row + 1):
            for column in range(merged_range.min_col, merged_range.max_col + 1):
                output[f"{get_column_letter(column)}{row}"] = {
                    "merged_range": str(merged_range), "value_origin": master.coordinate,
                    "value": master.value, "is_formula": master.data_type == "f",
                }
    return output


def excel_cell_context(cell: Any, merged: dict[str, dict[str, Any]]) -> dict[str, Any]:
    context = merged.get(cell.coordinate) or {}
    effective_value = cell.value if cell.value is not None else context.get("value")
    return {
        "cell": cell.coordinate, "column_index": cell.column,
        "raw_value": str(cell.value) if cell.value is not None else None,
        "effective_value": str(effective_value) if effective_value is not None else None,
        "is_formula": cell.data_type == "f" or bool(context.get("is_formula")),
        "is_dispimg": is_dispimg_formula(effective_value),
        "value_origin": context.get("value_origin") or cell.coordinate,
        "merged_range": context.get("merged_range"),
    }


def excel_field_pair(
    row: Any, cells: list[dict[str, Any]], roles: dict[int, str], *, is_header: bool
) -> tuple[str | None, str | None]:
    if is_header:
        return None, None
    labels = [column for column, role in roles.items() if role == "label"]
    values = [column for column, role in roles.items() if role == "value"]
    if len(labels) == len(values) == 1:
        label, value = cells[labels[0] - 1], cells[values[0] - 1]
    elif not roles:
        occupied = [cell for cell in cells if cell["raw_value"] is not None and not cell["is_dispimg"]]
        if len(occupied) != 2:
            return None, None
        label, value = occupied
        original_label = row[label["column_index"] - 1].value
        original_value = row[value["column_index"] - 1].value
        if not isinstance(original_label, str) or any(mark in original_label for mark in "\n。；;：:"):
            return None, None
        # Without a header, only recognize clear key/value rows. Two arbitrary
        # text cells (for example protocol names) remain an ordinary table row.
        label_is_field = any(term in original_label for term in EXCEL_FIELD_LABEL_TERMS)
        if not label_is_field and not isinstance(original_value, (int, float, bool)) and not re.search(r"\d", str(original_value)):
            return None, None
    else:
        return None, None
    label_text, value_text = label["effective_value"], value["effective_value"]
    if (
        not label_text or not label_text.strip() or value_text is None or not value_text.strip()
        or label["is_formula"] or value["is_formula"] or label["is_dispimg"] or value["is_dispimg"]
        or excel_header_role(label_text) in {"label", "value"} or excel_header_role(value_text) in {"label", "value"}
    ):
        return None, None
    return label_text, value_text


def excel_field_schema_descriptor(
    cells: list[dict[str, Any]], roles: dict[int, str], headers: dict[int, str], *,
    field_name: str | None, is_header: bool,
) -> dict[str, Any] | None:
    """Read field labels independently of empty/formula/image value cells."""
    if is_header:
        return None
    labels = [column for column, role in roles.items() if role == "label"]
    name = field_name
    if name is None and len(labels) == 1:
        label = cells[labels[0] - 1]
        if not label["is_formula"] and not label["is_dispimg"]:
            candidate = label["effective_value"]
            if isinstance(candidate, str) and candidate.strip() and excel_header_role(candidate) not in {"label", "value"}:
                name = candidate
    elif name is None and not roles:
        occupied = [cell for cell in cells if cell["raw_value"] is not None and not cell["is_dispimg"]]
        if 1 <= len(occupied) <= 2:
            label = occupied[0]
            candidate = label["effective_value"]
            if (
                isinstance(candidate, str) and not label["is_formula"]
                and not any(mark in candidate for mark in "\n。；;：:")
                and any(term in candidate for term in EXCEL_FIELD_LABEL_TERMS)
            ):
                name = candidate
    if not isinstance(name, str) or not name.strip():
        return None
    units = {unit.casefold(): unit for unit in (
        "kVA", "kW", "MW", "W", "A", "V", "kV", "℃", "°C", "%", "m²", "m2", "㎡",
        "台", "路", "个", "小时", "h", "min", "分钟", "mm", "m", "kWh", "MWh", "Hz",
    )}
    unit = None
    for column, role in roles.items():
        cell = cells[column - 1]
        if role == "unit" and not cell["is_formula"] and not cell["is_dispimg"]:
            unit = units.get(str(cell["effective_value"] or "").strip().casefold())
            if unit:
                break
    if unit is None:
        # Units can be declared in labels/headers, never inferred from a value.
        for text in [name, *headers.values()]:
            for declared in re.findall(r"[（(\[]([^）)\]]+)[）)\]]", text):
                unit = units.get(declared.strip().casefold())
                if unit:
                    break
            if unit:
                break
    return {"field_name": name, "column_headers": [headers[column] for column in sorted(headers)], "unit": unit}


def docx_heading_level(paragraph: Any) -> int | None:
    from docx.oxml.ns import qn

    style = paragraph.style
    for name in (style.style_id, style.name):
        match = re.fullmatch(r"(?:Heading|标题)\s*(\d+)", name, flags=re.IGNORECASE)
        if match:
            return int(match[1])
    if style.style_id == "Title":
        return 0
    properties = paragraph._p.pPr
    outline = properties.find(qn("w:outlineLvl")) if properties is not None else None
    if outline is not None:
        level = int(outline.get(qn("w:val"), "9"))
        return level + 1 if level < 9 else None
    return None


def extract_xlsx_chunks(
    path: Path,
    root: Path,
    *,
    namespace: str = "",
    knowledge_base_id: str = "",
    image_output_dir: Path | None = None,
) -> Iterable[dict[str, Any]]:
    from openpyxl import load_workbook

    registry_rows = (
        materialize_xlsx_dispimg_registry(
            path,
            root=root,
            output_dir=image_output_dir,
            namespace=namespace,
            knowledge_base_id=knowledge_base_id,
        )
        if image_output_dir
        else []
    )
    attachments_by_row = registry_by_sheet_row(registry_rows)
    workbook = load_workbook(path, read_only=False, data_only=False)
    try:
        rel = relative_path(path, root)
        for sheet in workbook.worksheets:
            merged_context = excel_merged_context(sheet)
            column_roles: dict[int, str] = {}
            header_values: dict[int, str] = {}
            for row_index, row in enumerate(sheet.iter_rows(), 1):
                row_attachments = [attachment_from_registry(item) for item in attachments_by_row.get((sheet.title, row_index), [])]
                source_cells = [cell for cell in row if cell.value is not None and not is_dispimg_formula(cell.value)]
                raw_source_text = " / ".join(str(cell.value) for cell in source_cells)
                all_source_cells = [cell for cell in row if cell.value is not None]
                if not raw_source_text.strip():
                    if not all_source_cells:
                        continue
                    # An image-only row retains its real formula/attachment
                    # identity, including failed/unavailable image mappings.
                    raw_source_text = " / ".join(str(cell.value) for cell in all_source_cells)
                cells = [excel_cell_context(cell, merged_context) for cell in row]
                detected_roles = {cell.column: excel_header_role(cell.value) for cell in source_cells if excel_header_role(cell.value)}
                is_header = "label" in detected_roles.values() and "value" in detected_roles.values()
                if is_header:
                    column_roles = detected_roles
                    header_values = {cell.column: str(cell.value) for cell in source_cells}
                field_name, field_value = excel_field_pair(row, cells, column_roles, is_header=is_header)
                schema_descriptor = excel_field_schema_descriptor(
                    cells, column_roles, header_values, field_name=field_name, is_header=is_header,
                )
                structural_path = list(dict.fromkeys(
                    str(cell["effective_value"]) for cell in cells
                    if cell["effective_value"] is not None and (
                        column_roles.get(cell["column_index"]) == "category"
                        or (cell.get("merged_range") and column_roles.get(cell["column_index"]) not in {"label", "value", "note"})
                    ) and str(cell["effective_value"]).strip()
                ))
                yield {
                    "text": raw_source_text,
                    "raw_source_text": raw_source_text,
                    "evidence_kind": "structured_field" if field_name is not None else "table_row",
                    "field_name": field_name,
                    "field_value": field_value,
                    "structural_path": structural_path,
                    "parser_type": "xlsx_row",
                    "relative_path": rel,
                    "anchor": f"{sheet.title}!row {row_index}",
                    "source_type": "uploaded_excel_row",
                    "sheet_name": sheet.title,
                    "row_index": row_index,
                    "cell_range": f"{all_source_cells[0].coordinate}:{all_source_cells[-1].coordinate}",
                    "metadata": {
                        "cells": cells, "column_headers": header_values, "row_role": "header" if is_header else "data",
                        **({"field_schema": schema_descriptor} if schema_descriptor else {}),
                    },
                    "proof_attachment_ids": [str(item["attachment_id"]) for item in row_attachments if item.get("attachment_id")],
                    "proof_attachments": row_attachments,
                }
    finally:
        workbook.close()


def extract_docx_chunks(path: Path, root: Path) -> Iterable[dict[str, Any]]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(path)
    rel = relative_path(path, root)
    headings: dict[int, str] = {}
    paragraph_index = 0
    table_index = 0
    for block_index, block in enumerate(document.iter_inner_content(), 1):
        if isinstance(block, Paragraph):
            paragraph_index += 1
            if not block.text.strip():
                continue
            level = docx_heading_level(block)
            if level is not None:
                headings = {key: value for key, value in headings.items() if key < level}
                headings[level] = block.text.strip()
            yield {
                "text": block.text,
                "raw_source_text": block.text,
                "evidence_kind": "paragraph",
                "paragraph_index": paragraph_index,
                "structural_path": [headings[key] for key in sorted(headings)],
                "metadata": {"block_index": block_index, "heading_level": level},
                "parser_type": "docx_paragraph", "relative_path": rel,
                "anchor": f"paragraph {paragraph_index}", "source_type": "uploaded_docx_paragraph",
            }
        elif isinstance(block, Table):
            table_index += 1
            for row_index, row in enumerate(block.rows, 1):
                raw_text = " / ".join(cell.text for cell in row.cells if cell.text.strip())
                if not raw_text.strip():
                    continue
                yield {
                    "text": raw_text,
                    "raw_source_text": raw_text,
                    "evidence_kind": "table_row",
                    "table_index": table_index,
                    "row_index": row_index,
                    "structural_path": [headings[key] for key in sorted(headings)],
                    "metadata": {"block_index": block_index},
                    "parser_type": "docx_table_row",
                    "relative_path": rel,
                    "anchor": f"table {table_index} row {row_index}",
                    "source_type": "uploaded_docx_table_row",
                }


def extract_text_chunks(path: Path, root: Path) -> Iterable[dict[str, str]]:
    rel = relative_path(path, root)
    # Preserve source code points, including CRLF, for exact quotation offsets.
    with path.open(encoding="utf-8", errors="replace", newline="") as source_file:
        text = source_file.read()
    for index, part in enumerate(split_text(text, MAX_CHUNK_CHARS, preserve_whitespace=True), 1):
        if part.strip():
            yield {
                "text": f"文件：{path.name}。片段：{index}。内容：{part}",
                "raw_source_text": part,
                "evidence_kind": "document_chunk",
                "parser_type": "text_chunk",
                "relative_path": rel,
                "anchor": f"text chunk {index}",
                "source_type": "uploaded_text_chunk",
            }


def is_dispimg_formula(value: Any) -> bool:
    return isinstance(value, str) and "DISPIMG(" in value


def proof_attachment_registry_from_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        for attachment in record.get("proof_attachments") or []:
            if not isinstance(attachment, dict):
                continue
            attachment_id = str(attachment.get("attachment_id") or "")
            if not attachment_id or attachment_id in seen:
                continue
            seen.add(attachment_id)
            rows.append(
                {
                    "attachment_id": attachment_id,
                    "file_id": attachment.get("file_id") or "",
                    "knowledge_base_id": record.get("knowledge_base_id") or "",
                    "namespace": record.get("namespace") or "",
                    "file_name": record.get("file_name") or "",
                    "relative_path": attachment.get("relative_path") or record.get("relative_path") or "",
                    "source_file_path": attachment.get("source_file_path") or "",
                    "sheet_name": attachment.get("sheet_name") or "",
                    "row_index": record.get("source", {}).get("row_index") or "",
                    "source_cell": attachment.get("source_cell") or "",
                    "image_id": attachment.get("image_id") or "",
                    "media_path": attachment.get("media_path") or "",
                    "media_content_type": attachment.get("media_content_type") or "",
                    "attachment_type": attachment.get("attachment_type") or "image",
                    "mapping_status": attachment.get("mapping_status") or "",
                    "image_path": attachment.get("image_path") or "",
                }
            )
    return rows


def upsert_records(
    *,
    qdrant: QdrantClient,
    collection_name: str,
    records: list[dict[str, Any]],
    embedder: EmbeddingClient,
    batch_size: int,
    namespace: str,
    point_counts: dict[str, int] | None = None,
    knowledge_base_id: str | None = None,
    index_version_id: str | None = None,
) -> tuple[int, int]:
    scope = None
    if index_version_id is not None:
        version = canonical_uuid(index_version_id, name="index_version_id")
        kb = canonical_uuid(knowledge_base_id, name="knowledge_base_id")
        scope = [{"collection": collection_name, "namespace": namespace, "knowledge_base_id": kb,
                  "index_version_id": version, "storage_contract": VERSIONED_STORAGE_CONTRACT}]
        if not records:
            raise RuntimeError("versioned ingestion requires nonempty native records")
        validate_hits_in_index_scopes(records, scope, collection_name=collection_name, namespaces=[namespace])
        for record in records:
            kind = "field_schema" if record.get("retrieval_object") == "field_schema" else "evidence"
            semantic_id = record.get("field_family_id") if kind == "field_schema" else record.get("chunk_id")
            if record.get("point_id") != versioned_point_id(version, semantic_id, object_kind=kind):
                raise IndexScopeError("versioned ingestion point_id is not bound to this candidate UUID")
    else:
        validate_hits_in_index_scopes(records, None, collection_name=collection_name, namespaces=[namespace])
        warnings.warn("Legacy ingestion upserts only unversioned points; no namespace is deleted.", LegacyIndexScopeWarning, stacklevel=2)
    dimension = 0
    upserted = 0
    collection_ready = qdrant.collection_exists(collection_name)
    if collection_ready and scope is not None:
        qdrant.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=build_index_scope_filter(scope, collection_name=collection_name, namespaces=[namespace])
            ),
            wait=True,
        )
    for batch in batches(records, max(1, batch_size)):
        vectors = embedder.embed([record["text_for_embedding"] for record in batch])
        if not vectors:
            if scope is not None:
                raise RuntimeError("candidate embedding batch returned no vectors")
            continue
        if dimension == 0:
            dimension = len(vectors[0])
        if not collection_ready:
            qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            )
            collection_ready = True
        points = []
        for record, vector in zip(batch, vectors, strict=True):
            if len(vector) != dimension:
                raise RuntimeError(f"embedding dimension changed from {dimension} to {len(vector)}")
            payload = {key: value for key, value in record.items() if key != "point_id"}
            points.append(models.PointStruct(id=record["point_id"], vector=vector, payload=payload))
        qdrant.upsert(collection_name=collection_name, points=points, wait=True)
        upserted += len(points)
        if point_counts is not None:
            for record in batch:
                role = "field_schema" if record.get("retrieval_object") == "field_schema" else "evidence"
                point_counts[role] = point_counts.get(role, 0) + 1
    return upserted, dimension


def validate_candidate_index(
    *,
    qdrant: QdrantClient,
    collection_name: str,
    namespace: str,
    knowledge_base_id: str,
    index_version_id: str,
    records: list[dict[str, Any]],
    schema_records: list[dict[str, Any]],
    input_snapshot: dict[str, Any],
    input_snapshot_hash: str,
) -> dict[str, Any]:
    version = canonical_uuid(index_version_id, name="index_version_id")
    kb = canonical_uuid(knowledge_base_id, name="knowledge_base_id")
    scopes = [{"collection": collection_name, "namespace": namespace, "knowledge_base_id": kb,
               "index_version_id": version, "storage_contract": VERSIONED_STORAGE_CONTRACT}]
    scope_filter = build_index_scope_filter(scopes, collection_name=collection_name, namespaces=[namespace])
    expected_by_id = {record["point_id"]: record for record in [*records, *schema_records]}
    expected_document_ids = {doc["document_id"] for doc in input_snapshot["documents"]}
    if not records or {record["document_id"] for record in records} != expected_document_ids:
        raise RuntimeError("candidate native evidence does not cover every frozen source document")
    actual_counts = {}
    for role in ("evidence", "field_schema"):
        query_filter = models.Filter(must=[scope_filter, models.FieldCondition(key="retrieval_object", match=models.MatchValue(value=role))])
        actual_counts[role] = qdrant.count(collection_name=collection_name, count_filter=query_filter, exact=True).count
    total = qdrant.count(collection_name=collection_name, count_filter=scope_filter, exact=True).count
    if (actual_counts["evidence"] != len(records) or actual_counts["field_schema"] != len(schema_records)
            or total != len(records) + len(schema_records)):
        raise RuntimeError("candidate Qdrant evidence/schema/total exact counts do not match ingestion manifests")

    representatives: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        representatives.setdefault((record["document_id"], record["evidence_kind"]), record)
    for record in schema_records:
        representatives.setdefault((record["document_id"], "field_schema"), record)
    for (_, kind), record in representatives.items():
        conditions = [scope_filter, models.FieldCondition(key="document_id", match=models.MatchValue(value=record["document_id"]))]
        if kind == "field_schema":
            conditions.extend([
                models.FieldCondition(key="retrieval_object", match=models.MatchValue(value="field_schema")),
                models.FieldCondition(key="field_family_id", match=models.MatchValue(value=record["field_family_id"])),
            ])
        else:
            conditions.extend([
                models.FieldCondition(key="retrieval_object", match=models.MatchValue(value="evidence")),
                models.FieldCondition(key="evidence_kind", match=models.MatchValue(value=kind)),
            ])
        stored = qdrant.retrieve(collection_name=collection_name, ids=[record["point_id"]], with_vectors=True, with_payload=False)
        if not stored or not isinstance(stored[0].vector, list) or not stored[0].vector:
            raise RuntimeError("candidate smoke point has no persisted dense vector")
        response = qdrant.query_points(collection_name=collection_name, query=stored[0].vector,
                                       query_filter=models.Filter(must=conditions), limit=1, with_payload=True)
        if not response.points:
            raise RuntimeError("candidate scoped native smoke retrieval returned no evidence")
        for point in response.points:
            payload = point.payload or {}
            validate_hits_in_index_scopes([payload], scopes, collection_name=collection_name, namespaces=[namespace])
            expected = expected_by_id.get(str(point.id))
            expected_payload = json.loads(json.dumps({key: value for key, value in expected.items() if key != "point_id"})) if expected is not None else None
            if expected_payload is None or payload != expected_payload:
                raise RuntimeError("candidate scoped smoke retrieval differs from its native ingestion manifest")
            if kind != "field_schema":
                raw = payload.get("raw_source_text")
                if not isinstance(raw, str) or not raw or payload.get("source_text_hash") != "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest():
                    raise RuntimeError("candidate native smoke source text/hash validation failed")
    return {
        "schema_version": "kb-index-validation-v1", "index_version_id": version, "knowledge_base_id": kb,
        "namespace": namespace, "collection": collection_name, "input_snapshot_hash": input_snapshot_hash,
        "expected_evidence_count": len(records), "actual_evidence_count": actual_counts["evidence"],
        "expected_schema_count": len(schema_records), "actual_schema_count": actual_counts["field_schema"],
        "document_count": len(input_snapshot["documents"]), "source_hashes_verified": True, "smoke_passed": True,
        "validated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def build_summary(
    *,
    records: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    collection_name: str,
    qdrant_path: Path,
    qdrant_url: str,
    namespace: str,
    embedding_endpoint: str,
    embedding_model: str,
    dimension: int,
    upserted: int,
    elapsed_seconds: float,
    schema_records: list[dict[str, Any]] | None = None,
    schema_upserted: int = 0,
    total_upserted: int | None = None,
) -> dict[str, Any]:
    return {
        "status": "completed",
        "engine": "gongkan_knowledge_ingestion",
        "collection_name": collection_name,
        "qdrant_path": str(qdrant_path),
        "qdrant_url": qdrant_url,
        "namespace": namespace,
        "record_count": len(records),
        "upserted_count": upserted,
        "schema_record_count": len(schema_records or []),
        "schema_upserted_count": schema_upserted,
        "total_point_count": len(records) + len(schema_records or []),
        "total_upserted_count": upserted + schema_upserted if total_upserted is None else total_upserted,
        "skipped_file_count": len(skipped),
        "dimension": dimension,
        "embedding_endpoint": embedding_endpoint,
        "embedding_model": embedding_model,
        "elapsed_seconds": elapsed_seconds,
        "image_proof_count": sum(len(record.get("proof_attachments") or []) for record in records),
        "counts_by_source_type": dict(Counter(record["source_type"] for record in records)),
        "counts_by_corpus_layer": dict(Counter(record["corpus_layer"] for record in records)),
        "counts_by_file": dict(Counter(record["relative_path"] for record in records)),
    }


def build_run_manifest(*, summary: dict[str, Any], namespace: str, knowledge_base_id: str, artifacts: dict[str, str | None]) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {
        "run_id": stable_chunk_id(knowledge_base_id, namespace, now, "ingestion"),
        "created_at": now,
        "finished_at": now,
        "status": "completed",
        "engine": "gongkan_knowledge_ingestion",
        "target_namespace": namespace,
        "room_context": "",
        "rows": "",
        "judge_enabled": False,
        "writeback_enabled": False,
        "artifacts": artifacts,
        "counts": {
            "total_fields": int(summary.get("record_count") or 0),
            "answered": 0,
            "partial_clue": 0,
            "not_found": 0,
            "conflict_unresolved": 0,
            "review_required": 0,
            "writeback_allowed": 0,
            "failed": 0,
        },
    }


def write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Knowledge Ingestion Summary",
        "",
        f"- status: `{summary['status']}`",
        f"- namespace: `{summary['namespace']}`",
        f"- collection: `{summary['collection_name']}`",
        f"- records: **{summary['record_count']}**",
        f"- upserted: **{summary['upserted_count']}**",
        f"- auxiliary schema points: **{summary.get('schema_record_count', 0)}**",
        f"- total upserted points: **{summary.get('total_upserted_count', summary['upserted_count'])}**",
        f"- skipped files: **{summary['skipped_file_count']}**",
        f"- embedding model: `{summary['embedding_model']}`",
        "",
        "## Records By Source Type",
        "",
    ]
    for key, value in sorted(summary["counts_by_source_type"].items()):
        lines.append(f"- `{key}`: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def split_text(text: str, max_chars: int, *, preserve_whitespace: bool = False) -> list[str]:
    if not preserve_whitespace:
        text = display_text(text)
    if len(text) <= max_chars:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end])
        start = end
    return chunks


def batches(records: list[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(records), batch_size):
        yield records[index : index + batch_size]


def stable_chunk_id(*parts: Any) -> str:
    text = "|".join(display_text(part) for part in parts)
    return "kb_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def dumps_summary(summary: dict[str, Any]) -> str:
    return json.dumps(
        {
            "status": summary["status"],
            "namespace": summary["namespace"],
            "collection_name": summary["collection_name"],
            "record_count": summary["record_count"],
            "upserted_count": summary["upserted_count"],
            "outcome": "ready_for_retrieval",
        },
        ensure_ascii=False,
    )
