"""Value-free Excel field identities for an auxiliary schema index.

Schema points select field families. They are not physical evidence and never
carry answers, quotations, or an evidence kind.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from nested_doc_rag.retrieval.version_scope import IndexScopeError, canonical_uuid, versioned_point_id

FIELD_SCHEMA_CONTRACT_VERSION = "field-schema-v1"


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        def column_order(key: Any) -> tuple[int, str]:
            text = str(key)
            return (int(text), "") if text.isdecimal() else (10**9, text)

        value = [value[key] for key in sorted(value, key=column_order)]
    return [_text(entry) for entry in value if _text(entry)] if isinstance(value, list) else []


@dataclass(frozen=True)
class FieldSchema:
    field_family_id: str
    namespace: str
    knowledge_base_id: str
    relative_path: str
    file_name: str
    sheet_name: str
    category_path: list[str]
    field_name: str
    column_headers: list[str]
    unit: str | None
    document_id: str | None = None
    index_version: str | None = None
    index_version_id: str | None = None

    @property
    def schema_text(self) -> str:
        parts = [f"字段：{self.field_name}"]
        if self.category_path:
            parts.append("类别：" + " / ".join(self.category_path))
        parts.append(f"工作表：{self.sheet_name}")
        if self.column_headers:
            parts.append("列头：" + " / ".join(self.column_headers))
        if self.unit:
            parts.append(f"单位：{self.unit}")
        return "。".join(parts)

    def to_payload(self) -> dict[str, Any]:
        # Version affects the point identity/scope, never the field family.
        identity = f"{self.field_family_id}|{self.index_version or ''}"
        schema_id = "schema_" + hashlib.sha256(identity.encode()).hexdigest()
        payload = {
            "schema_id": schema_id, "point_id": str(uuid.uuid5(uuid.NAMESPACE_URL, schema_id)),
            "retrieval_object": "field_schema", "corpus_layer": "field_schema",
            "source_type": "excel_field_schema", "schema_contract_version": FIELD_SCHEMA_CONTRACT_VERSION,
            "field_family_id": self.field_family_id, "namespace": self.namespace,
            "knowledge_base_id": self.knowledge_base_id, "document_id": self.document_id,
            "index_version": self.index_version, "relative_path": self.relative_path,
            "file_name": self.file_name, "sheet_name": self.sheet_name,
            "category_path": list(self.category_path), "field_name": self.field_name,
            "column_headers": list(self.column_headers), "unit": self.unit,
            "schema_text": self.schema_text, "text_for_embedding": self.schema_text,
        }
        if self.index_version_id is not None:
            version = canonical_uuid(self.index_version_id, name="index_version_id")
            payload.update(index_version=version, index_version_id=version,
                           point_id=versioned_point_id(version, self.field_family_id, object_kind="field_schema"))
        return payload


def field_schema_for_record(record: Mapping[str, Any]) -> FieldSchema | None:
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    descriptor = metadata.get("field_schema")
    descriptor = descriptor if isinstance(descriptor, Mapping) else {}
    source = record.get("source")
    source = source if isinstance(source, Mapping) else {}
    path = _text(record.get("relative_path") or source.get("relative_path") or record.get("file_name")).replace("\\", "/")
    if record.get("retrieval_object") == "field_schema" or not (
        record.get("source_type") in {"uploaded_excel_row", "main_excel_capability"}
        or path.casefold().endswith((".xlsx", ".xlsm"))
    ):
        return None
    field_name = _text(descriptor.get("field_name") or record.get("field_name"))
    sheet = _text(record.get("sheet_name") or source.get("sheet_name"))
    namespace = _text(record.get("namespace") or source.get("namespace"))
    if not field_name or field_name.startswith("=") or not path or not sheet or not namespace:
        return None
    category = _strings(descriptor.get("category_path") or record.get("structural_path") or source.get("category_path"))
    headers = _strings(descriptor.get("column_headers") or metadata.get("column_headers"))
    unit = _text(descriptor.get("unit")) or None
    kb_id = _text(record.get("knowledge_base_id") or source.get("knowledge_base_id"))
    identity = {
        "namespace": namespace, "knowledge_base_id": kb_id, "relative_path": path,
        "sheet_name": sheet, "category_path": category, "field_name": field_name,
        "column_headers": headers, "unit": unit,
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    version_id = _text(record.get("index_version_id") or source.get("index_version_id")) or None
    version = _text(record.get("index_version") or source.get("index_version")) or None
    if version_id is not None:
        version_id = canonical_uuid(version_id, name="index_version_id")
        if version is not None and canonical_uuid(version, name="index_version") != version_id:
            raise IndexScopeError("field schema index_version alias disagrees with its UUID")
        version = version_id
    return FieldSchema(
        field_family_id="family_" + digest, namespace=namespace, knowledge_base_id=kb_id,
        relative_path=path, file_name=_text(record.get("file_name")), sheet_name=sheet,
        category_path=category, field_name=field_name, column_headers=headers, unit=unit,
        document_id=_text(record.get("document_id") or source.get("document_id")) or None,
        index_version=version,
        index_version_id=version_id,
    )


def bind_field_schema_families(value_records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return detached evidence records with derived, structural family binding."""
    bound = []
    for record in value_records:
        if record.get("retrieval_object") == "field_schema":
            raise ValueError("auxiliary schema points cannot be bound as evidence")
        copied = deepcopy(dict(record))
        copied["retrieval_object"] = "evidence"
        copied.pop("field_family_id", None)
        schema = field_schema_for_record(record)
        if schema is not None:
            copied["field_family_id"] = schema.field_family_id
        bound.append(copied)
    return bound


def build_field_schema_records(value_records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build one auxiliary point per family/version; never copy value payloads."""
    schemas: dict[tuple[str, str | None], dict[str, Any]] = {}
    for record in value_records:
        schema = field_schema_for_record(record)
        if schema is not None:
            key = (schema.field_family_id, schema.index_version)
            schemas.setdefault(key, schema.to_payload())
    return [schemas[key] for key in sorted(schemas, key=lambda key: (key[0], key[1] or ""))]
