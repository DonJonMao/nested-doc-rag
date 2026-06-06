from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExcelWritebackItem:
    sheet_name: str
    cell: str
    value: Any
    comment: str | None = None


@dataclass(frozen=True)
class WritebackAuditRecord:
    field_id: str
    row_index: int
    target_cell: str | None
    action: str
    reason: str
    answer_status: str
    confidence: float
    sheet_name: str | None = None
    cell: str | None = None
    answer_value: Any = None
    source_chunk_ids: list[str] = field(default_factory=list)
    evidence_attachment_ids: list[str] = field(default_factory=list)
    trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "row_index": self.row_index,
            "target_cell": self.target_cell,
            "sheet_name": self.sheet_name,
            "cell": self.cell,
            "action": self.action,
            "reason": self.reason,
            "answer_status": self.answer_status,
            "confidence": self.confidence,
            "answer_value": self.answer_value,
            "source_chunk_ids": self.source_chunk_ids,
            "evidence_attachment_ids": self.evidence_attachment_ids,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class ReviewItem:
    field_id: str
    row_index: int
    target_cell: str | None
    reason: str
    answer_status: str
    confidence: float
    answer_value: Any = None
    source_chunk_ids: list[str] = field(default_factory=list)
    evidence_attachment_ids: list[str] = field(default_factory=list)
    trace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "row_index": self.row_index,
            "target_cell": self.target_cell,
            "reason": self.reason,
            "answer_status": self.answer_status,
            "confidence": self.confidence,
            "answer_value": self.answer_value,
            "source_chunk_ids": self.source_chunk_ids,
            "evidence_attachment_ids": self.evidence_attachment_ids,
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True)
class WritebackSummary:
    output_path: Path
    audit_path: Path
    evidence_map_path: Path
    review_items_path: Path
    total_count: int
    written_count: int
    skipped_count: int
    conflict_count: int
    invalid_count: int
    formula_skipped_count: int
    review_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "audit_path": str(self.audit_path),
            "evidence_map_path": str(self.evidence_map_path),
            "review_items_path": str(self.review_items_path),
            "total_count": self.total_count,
            "written_count": self.written_count,
            "skipped_count": self.skipped_count,
            "conflict_count": self.conflict_count,
            "invalid_count": self.invalid_count,
            "formula_skipped_count": self.formula_skipped_count,
            "review_count": self.review_count,
        }
