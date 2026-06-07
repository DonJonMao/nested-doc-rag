from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldConstraints:
    enum_values: list[str] = field(default_factory=list)
    regex: str = ""
    min: float | None = None
    max: float | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> FieldConstraints:
        value = value or {}
        return cls(
            enum_values=[str(item) for item in value.get("enum_values") or []],
            regex=str(value.get("regex") or ""),
            min=float(value["min"]) if value.get("min") is not None else None,
            max=float(value["max"]) if value.get("max") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enum_values": self.enum_values,
            "regex": self.regex,
            "min": self.min,
            "max": self.max,
        }


@dataclass(frozen=True)
class FieldGold:
    field_id: str
    row_index: int
    target_cell: str | None
    question_text: str
    expected_value: Any
    expected_status: str = "answered"
    accepted_aliases: list[str] = field(default_factory=list)
    field_type: str = "text"
    required: bool = True
    must_have_evidence: bool = False
    gold_source_chunk_ids: list[str] = field(default_factory=list)
    constraints: FieldConstraints = field(default_factory=FieldConstraints)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FieldGold:
        return cls(
            field_id=str(value["field_id"]),
            row_index=int(value["row_index"]),
            target_cell=value.get("target_cell"),
            question_text=str(value.get("question_text") or ""),
            expected_value=value.get("expected_value"),
            expected_status=str(value.get("expected_status") or "answered"),
            accepted_aliases=[str(item) for item in value.get("accepted_aliases") or []],
            field_type=str(value.get("field_type") or "text"),
            required=bool(value.get("required", True)),
            must_have_evidence=bool(value.get("must_have_evidence", False)),
            gold_source_chunk_ids=[str(item) for item in value.get("gold_source_chunk_ids") or []],
            constraints=FieldConstraints.from_dict(value.get("constraints")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "row_index": self.row_index,
            "target_cell": self.target_cell,
            "question_text": self.question_text,
            "expected_value": self.expected_value,
            "expected_status": self.expected_status,
            "accepted_aliases": self.accepted_aliases,
            "field_type": self.field_type,
            "required": self.required,
            "must_have_evidence": self.must_have_evidence,
            "gold_source_chunk_ids": self.gold_source_chunk_ids,
            "constraints": self.constraints.to_dict(),
        }


@dataclass(frozen=True)
class FieldPrediction:
    field_id: str
    row_index: int
    target_cell: str | None
    answer_value: Any
    answer_status: str = "answered"
    confidence: float = 0.0
    source_chunk_ids: list[str] = field(default_factory=list)
    evidence_attachment_ids: list[str] = field(default_factory=list)
    reference_chunk_ids: list[str] = field(default_factory=list)
    reference_source_documents: list[dict[str, Any]] = field(default_factory=list)
    reference_snippets: list[str] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    method_name: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> FieldPrediction:
        return cls(
            field_id=str(value["field_id"]),
            row_index=int(value["row_index"]),
            target_cell=value.get("target_cell"),
            answer_value=value.get("answer_value"),
            answer_status=str(value.get("answer_status") or "answered"),
            confidence=float(value.get("confidence") or 0.0),
            source_chunk_ids=[str(item) for item in value.get("source_chunk_ids") or []],
            evidence_attachment_ids=[str(item) for item in value.get("evidence_attachment_ids") or []],
            reference_chunk_ids=[str(item) for item in value.get("reference_chunk_ids") or []],
            reference_source_documents=[dict(item) for item in value.get("reference_source_documents") or [] if isinstance(item, dict)],
            reference_snippets=[str(item) for item in value.get("reference_snippets") or []],
            validation=dict(value.get("validation") or {}),
            method_name=str(value.get("method_name") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "row_index": self.row_index,
            "target_cell": self.target_cell,
            "answer_value": self.answer_value,
            "answer_status": self.answer_status,
            "confidence": self.confidence,
            "source_chunk_ids": self.source_chunk_ids,
            "evidence_attachment_ids": self.evidence_attachment_ids,
            "reference_chunk_ids": self.reference_chunk_ids,
            "reference_source_documents": self.reference_source_documents,
            "reference_snippets": self.reference_snippets,
            "validation": self.validation,
            "method_name": self.method_name,
        }


@dataclass(frozen=True)
class FieldMetricRow:
    field_id: str
    row_index: int
    target_cell: str | None
    question_text: str
    field_type: str
    expected_value: Any
    answer_value: Any
    expected_status: str
    answer_status: str
    confidence: float
    exact_match: bool
    semantic_match: bool
    status_match: bool
    evidence_supported: bool
    evidence_recall_at_k: float | None
    abstention_correct: bool | None
    constraint_violations: list[str] = field(default_factory=list)
    needs_human_review: bool = False
    correction_required: bool = False
    badcase_categories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "row_index": self.row_index,
            "target_cell": self.target_cell,
            "question_text": self.question_text,
            "field_type": self.field_type,
            "expected_value": self.expected_value,
            "answer_value": self.answer_value,
            "expected_status": self.expected_status,
            "answer_status": self.answer_status,
            "confidence": self.confidence,
            "exact_match": self.exact_match,
            "semantic_match": self.semantic_match,
            "status_match": self.status_match,
            "evidence_supported": self.evidence_supported,
            "evidence_recall_at_k": self.evidence_recall_at_k,
            "abstention_correct": self.abstention_correct,
            "constraint_violations": self.constraint_violations,
            "needs_human_review": self.needs_human_review,
            "correction_required": self.correction_required,
            "badcase_categories": self.badcase_categories,
        }


@dataclass(frozen=True)
class EvalItem:
    form_item_id: str
    file_name: str
    sheet_name: str
    row_index: int
    target_cell: str | None
    question_text: str | None
    instruction_text: str | None = None
    answer_example: str | None = None
    heldout_answer: str | None = None
    category_path: list[str] = field(default_factory=list)
    needs_evidence: bool = False


@dataclass(frozen=True)
class EvalResult:
    row_index: int
    generated_answer: dict[str, Any]
    judge: dict[str, Any]
    top_hits: list[dict[str, Any]] = field(default_factory=list)
