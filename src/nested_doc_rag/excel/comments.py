from __future__ import annotations

from typing import Any

from nested_doc_rag.schemas.eval import FieldPrediction


def format_source_comment(source: str, note: str | None = None) -> str:
    return source if not note else f"{source}\n{note}"


def build_prediction_comment(prediction: FieldPrediction, *, trace_id: str | None = None) -> str:
    lines = [
        f"confidence: {prediction.confidence:.4f}",
        f"source_chunk_ids: {format_list(prediction.source_chunk_ids)}",
        f"evidence_attachment_ids: {format_list(prediction.evidence_attachment_ids)}",
        f"answer_status: {prediction.answer_status}",
        f"trace_id: {trace_id or prediction.validation.get('trace_id') or ''}",
    ]
    return "\n".join(lines)


def format_list(values: list[Any]) -> str:
    return ", ".join(str(value) for value in values)
