"""Typed addresses and native source identity for retrieved evidence."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from typing import Any


def _optional_index(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("evidence indices must be positive integers")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError("evidence indices must be positive integers")
    index = int(value)
    if index <= 0:
        raise ValueError("evidence indices must be positive integers")
    return index


@dataclass(frozen=True)
class EvidenceRef:
    chunk_id: str
    knowledge_base_id: str
    namespace: str
    file_name: str
    relative_path: str
    evidence_kind: str
    sheet_name: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    cell_range: str | None = None
    paragraph_index: int | None = None
    source_anchor: str | None = None
    source_text: str | None = None
    attachment_ids: list[str] = field(default_factory=list)
    document_id: str | None = None
    index_version: str | None = None
    source_text_hash: str | None = None
    source_text_hash_space: str | None = None
    source_text_policy: str | None = None
    source_document_hash: str | None = None
    qdrant_point_id: str | None = None
    table_id: str | None = None
    source_row_indices: list[int] = field(default_factory=list)
    embedded_file_name: str | None = None
    parent_file_id: str | None = None
    parent_source_cell: str | None = None
    source_chain: list[dict[str, Any]] = field(default_factory=list)
    local_anchor: dict[str, Any] = field(default_factory=dict)
    proof_attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    quote: str | None = None
    start: int | None = None
    end: int | None = None

    @property
    def evidence_id(self) -> str:
        return self.chunk_id

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidenceRef:
        if not isinstance(value, Mapping):
            raise ValueError("evidence_ref must be an object")
        names = {definition.name for definition in fields(cls)}
        text_names = names - {
            "table_index", "row_index", "column_index", "paragraph_index", "start", "end", "attachment_ids",
            "source_row_indices", "source_chain", "local_anchor", "proof_attachments", "metadata",
        }
        values: dict[str, Any] = {}
        required = {"chunk_id", "knowledge_base_id", "namespace", "file_name", "relative_path", "evidence_kind"}
        for name in text_names:
            raw = value.get(name)
            values[name] = str(raw) if raw is not None else ("" if name in required else None)
        values["chunk_id"] = str(value.get("chunk_id") or value.get("evidence_id") or "")
        for name in ("table_index", "row_index", "column_index", "paragraph_index"):
            values[name] = _optional_index(value.get(name))
        for name in ("start", "end"):
            raw = value.get(name)
            if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw < 0):
                raise ValueError("evidence quote offsets must be nonnegative integers")
            values[name] = raw
        for name in ("attachment_ids", "source_row_indices"):
            raw = value.get(name)
            raw = [] if raw is None else raw
            if not isinstance(raw, list):
                raise ValueError(f"evidence {name} must be a list")
            if name == "attachment_ids":
                values[name] = [str(item) for item in raw]
            else:
                values[name] = [_optional_index(item) for item in raw]
                if any(item is None for item in values[name]):
                    raise ValueError("evidence source_row_indices must be positive integers")
        for name in ("source_chain", "proof_attachments"):
            raw = value.get(name)
            raw = [] if raw is None else raw
            if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
                raise ValueError(f"evidence {name} must be a list of objects")
            values[name] = deepcopy(raw)
        for name in ("local_anchor", "metadata"):
            raw = value.get(name)
            raw = {} if raw is None else raw
            if not isinstance(raw, Mapping):
                raise ValueError(f"evidence {name} must be an object")
            values[name] = deepcopy(dict(raw))
        values["metadata"].update({name: deepcopy(raw) for name, raw in value.items() if name not in names and name != "evidence_id"})
        if value.get("evidence_id") and value.get("chunk_id") and value["evidence_id"] != value["chunk_id"]:
            values["metadata"]["evidence_id"] = deepcopy(value["evidence_id"])
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
