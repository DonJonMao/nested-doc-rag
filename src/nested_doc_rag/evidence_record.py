"""Canonical evidence shape, with lossless adapters for existing payloads.

Evidence kind describes physical content. It does not replace source origin,
corpus policy, or the grounding rules that decide whether an answer is safe.
"""
from __future__ import annotations

import copy
import hashlib
import re
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

EVIDENCE_KINDS = frozenset({"structured_field", "table_row", "paragraph", "document_chunk", "document_intro"})
LEGACY_SOURCE_KINDS = {
    "main_excel_capability": "structured_field",
    "embedded_word_table": "table_row",
    "embedded_raw_segment": "document_chunk",
    "intro_doc_paragraph": "document_intro",
    "intro_doc_table_row": "table_row",
    "uploaded_excel_row": "table_row",
    "uploaded_docx_paragraph": "paragraph",
    "uploaded_docx_table_row": "table_row",
    "uploaded_text_chunk": "document_chunk",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _index(value: Any) -> int | None:
    # Legacy embedded row_index can be the *parent* cell, such as E90.
    # It must not be treated as an integer row in the embedded document.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def infer_evidence_kind(payload: Mapping[str, Any]) -> str:
    explicit = payload.get("evidence_kind")
    if explicit is not None:
        if explicit not in EVIDENCE_KINDS:
            raise ValueError(f"unsupported evidence_kind: {explicit!r}")
        return str(explicit)
    source = payload.get("source") or {}
    source = source if isinstance(source, Mapping) else {}
    source_type = _text(payload.get("source_type"))
    if source_type == "embedded_raw_segment":
        segment_type = _text(payload.get("segment_type") or source.get("segment_type"))
        if segment_type in {"embedded_docx_paragraph", "embedded_doc_paragraph", "docx_paragraph"}:
            return "paragraph"
        if segment_type in {"embedded_docx_table_row", "embedded_xlsx_row", "docx_table_row", "xlsx_row"}:
            return "table_row"
    return LEGACY_SOURCE_KINDS.get(source_type, "document_chunk")


@dataclass(frozen=True)
class EvidenceAddress:
    file_name: str = ""
    relative_path: str = ""
    sheet_name: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    cell_range: str | None = None
    paragraph_index: int | None = None
    source_anchor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> EvidenceAddress:
        source = payload.get("source") or {}
        source = source if isinstance(source, Mapping) else {}
        address = payload.get("address") or {}
        address = address if isinstance(address, Mapping) else {}
        source_anchor = payload.get("source_anchor") or source.get("source_anchor") or {}
        source_anchor = source_anchor if isinstance(source_anchor, Mapping) else {}
        local_anchor = source.get("local_anchor") or payload.get("local_anchor") or {}
        local_anchor = local_anchor if isinstance(local_anchor, Mapping) else {}

        def first(*keys: str) -> Any:
            for record in (address, payload, source, source_anchor, local_anchor):
                for key in keys:
                    value = record.get(key)
                    if value is not None and value != "":
                        return value
            return None

        def index(key: str) -> int | None:
            for record in (address, payload, source, source_anchor, local_anchor):
                value = _index(record.get(key))
                if value is not None:
                    return value
            return None

        # Old source_anchor sometimes is already a typed location object.
        anchor_text = next((
            _text(record[key]) for record in (address, payload, source)
            for key in ("source_anchor", "anchor") if isinstance(record.get(key), str) and record[key]
        ), "")
        paragraph_index = index("paragraph_index")
        table_index = index("table_index")
        row_index = index("row_index")
        if _index(address.get("row_index")) is None and _index(local_anchor.get("row_index")) is not None:
            row_index = _index(local_anchor["row_index"])
        if payload.get("source_type") == "intro_doc_paragraph":
            paragraph_index = paragraph_index or row_index
            row_index = None
        paragraph_match = re.fullmatch(r"paragraph (\d+)", anchor_text)
        table_match = re.fullmatch(r"table (\d+) row (\d+)", anchor_text)
        if paragraph_match and paragraph_index is None:
            paragraph_index = int(paragraph_match[1])
        if table_match:
            table_index = table_index or int(table_match[1])
            row_index = row_index or int(table_match[2])
        return cls(
            file_name=_text(first("file_name", "source_document")),
            relative_path=_text(first("relative_path")),
            sheet_name=_text(first("sheet_name")) or None,
            table_index=table_index,
            row_index=row_index,
            column_index=index("column_index"),
            cell_range=_text(first("cell_range", "cell", "source_cell")) or None,
            paragraph_index=paragraph_index,
            source_anchor=anchor_text or None,
        )


@dataclass(frozen=True)
class EvidenceRecord:
    chunk_id: str
    point_id: str
    namespace: str
    knowledge_base_id: str
    evidence_kind: str
    corpus_layer: str
    raw_text: str
    text_for_embedding: str
    address: EvidenceAddress
    structural_path: list[str] = field(default_factory=list)
    field_name: str | None = None
    field_value: str | None = None
    proof_attachment_ids: list[str] = field(default_factory=list)
    proof_attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_source_text: str | None = None

    @property
    def evidence_id(self) -> str:
        return self.chunk_id

    @classmethod
    def from_legacy(cls, payload: Mapping[str, Any]) -> EvidenceRecord:
        chunk_id = _text(payload.get("chunk_id") or payload.get("evidence_id"))
        if not chunk_id:
            raise ValueError("evidence record requires chunk_id or evidence_id")
        canonical_keys = {
            "chunk_id", "evidence_id", "point_id", "namespace", "knowledge_base_id", "evidence_kind", "corpus_layer",
            "raw_text", "raw_source_text", "text_for_embedding", "address", "structural_path", "field_name", "field_value",
            "proof_attachment_ids", "proof_attachments", "metadata",
        }
        metadata = copy.deepcopy(dict(payload.get("metadata") or {}))
        metadata.update({key: copy.deepcopy(value) for key, value in payload.items() if key not in canonical_keys})
        metadata.setdefault("original_source_type", _text(payload.get("source_type")))
        source = payload.get("source") or {}
        source = source if isinstance(source, Mapping) else {}
        structural_path = payload.get("structural_path") or payload.get("heading_path") or payload.get("category_path") or []
        if not structural_path:
            structural_path = source.get("heading_path") or source.get("category_path") or []
        if isinstance(structural_path, str):
            structural_path = [structural_path]
        raw_source = payload.get("raw_source_text")
        if raw_source is None:
            raw_source = source.get("raw_source_text")
        for key in ("source_text_hash", "source_text_hash_space"):
            if key not in metadata and source.get(key) is not None:
                metadata[key] = copy.deepcopy(source[key])
        if isinstance(raw_source, str):
            if not metadata.get("source_text_hash"):
                metadata["source_text_hash"] = "sha256:" + hashlib.sha256(raw_source.encode("utf-8")).hexdigest()
                metadata.setdefault("source_text_hash_space", "raw_source_text")
        else:
            raw_source = None
        raw_text = _text(payload.get("raw_text"))
        field_name = payload.get("field_name")
        field_value = payload.get("field_value")
        # Existing structured labels/values may be copied, never reconstructed
        # from a normalized row or an embedding string.
        if field_name is None:
            field_name = source.get("field_name")
        if field_value is None:
            field_value = source.get("field_value")
        if payload.get("source_type") == "main_excel_capability":
            if field_name is None:
                field_name = next((
                    record[key] for record in (payload, source)
                    for key in ("capability_desc", "question_text") if record.get(key) is not None
                ), None)
            if field_value is None:
                field_value = next((
                    record["answer_value"] for record in (payload, source) if record.get("answer_value") is not None
                ), None)
        return cls(
            chunk_id=chunk_id,
            point_id=_text(payload.get("point_id")) or str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)),
            namespace=_text(payload.get("namespace")),
            knowledge_base_id=_text(payload.get("knowledge_base_id") or source.get("knowledge_base_id")),
            evidence_kind=infer_evidence_kind(payload),
            corpus_layer=_text(payload.get("corpus_layer")) or "fact",
            raw_text=raw_text,
            text_for_embedding=_text(payload.get("text_for_embedding")) or raw_text,
            address=EvidenceAddress.from_payload(payload),
            structural_path=[_text(value) for value in structural_path],
            field_name=_text(field_name) if field_name is not None else None,
            field_value=_text(field_value) if field_value is not None else None,
            proof_attachment_ids=[_text(value) for value in payload.get("proof_attachment_ids") or []],
            proof_attachments=copy.deepcopy(payload.get("proof_attachments") or []),
            metadata=metadata,
            raw_source_text=raw_source,
        )

    def to_payload(self) -> dict[str, Any]:
        if self.evidence_kind not in EVIDENCE_KINDS:
            raise ValueError(f"unsupported evidence_kind: {self.evidence_kind!r}")
        payload = copy.deepcopy(self.metadata)
        payload.update({
            "chunk_id": self.chunk_id,
            "evidence_id": self.evidence_id,
            "point_id": self.point_id,
            "namespace": self.namespace,
            "knowledge_base_id": self.knowledge_base_id,
            "evidence_kind": self.evidence_kind,
            "corpus_layer": self.corpus_layer,
            "raw_text": self.raw_text,
            "text_for_embedding": self.text_for_embedding,
            "address": self.address.to_dict(),
            "structural_path": list(self.structural_path),
            "field_name": self.field_name,
            "field_value": self.field_value,
            "proof_attachment_ids": list(self.proof_attachment_ids),
            "proof_attachments": copy.deepcopy(self.proof_attachments),
            "metadata": copy.deepcopy(self.metadata),
        })
        if self.raw_source_text is not None:
            payload["raw_source_text"] = self.raw_source_text
        # Keep the established top-level location fields for existing consumers.
        for key, value in self.address.to_dict().items():
            if value is not None and value != "":
                legacy_key = key if key != "source_anchor" else "anchor"
                if payload.get(legacy_key) in (None, ""):
                    payload[legacy_key] = value
                    payload["metadata"][legacy_key] = copy.deepcopy(value)
        return payload


def normalize_evidence_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    return EvidenceRecord.from_legacy(payload).to_payload()
