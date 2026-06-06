from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.comments import Comment
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string, get_column_letter
from openpyxl.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from nested_doc_rag.excel.comments import build_prediction_comment
from nested_doc_rag.io import read_json, read_jsonl, write_json, write_jsonl
from nested_doc_rag.schemas.eval import FieldPrediction
from nested_doc_rag.schemas.excel import ExcelWritebackItem, ReviewItem, WritebackAuditRecord, WritebackSummary

Mode = Literal["safe", "overwrite"]

MAX_EXCEL_ROW = 1_048_576
MAX_EXCEL_COLUMN = 16_384
COMMENT_AUTHOR = "nested-doc-rag"


@dataclass(frozen=True)
class TargetCell:
    sheet_name: str
    cell: str
    key: str


@dataclass(frozen=True)
class ResolvedPrediction:
    prediction: FieldPrediction
    target: TargetCell


def prepare_writeback_item(sheet_name: str, cell: str, value: object, comment: str | None = None) -> ExcelWritebackItem:
    return ExcelWritebackItem(sheet_name=sheet_name, cell=cell, value=value, comment=comment)


def writeback_from_files(
    *,
    template_path: Path,
    predictions_path: Path,
    output_path: Path,
    trace_path: Path | None = None,
    evidence_map_path: Path | None = None,
    mode: Mode = "safe",
    write_comments: bool = True,
) -> WritebackSummary:
    predictions = [FieldPrediction.from_dict(record) for record in read_jsonl(predictions_path)]
    trace_by_field = load_trace_index(trace_path) if trace_path else {}
    evidence_map = read_json(evidence_map_path) if evidence_map_path and evidence_map_path.exists() else None
    return patch_workbook(
        template_path=template_path,
        predictions=predictions,
        output_path=output_path,
        mode=mode,
        write_comments=write_comments,
        trace_by_field=trace_by_field,
        evidence_map=evidence_map,
    )


def patch_workbook(
    template_path: Path,
    predictions: list[FieldPrediction],
    output_path: Path,
    mode: Mode = "safe",
    write_comments: bool = True,
    *,
    trace_by_field: dict[str, str] | None = None,
    evidence_map: dict[str, Any] | None = None,
) -> WritebackSummary:
    if mode not in {"safe", "overwrite"}:
        raise ValueError("mode must be 'safe' or 'overwrite'")

    workbook = load_workbook(template_path)
    trace_by_field = trace_by_field or {}
    audit_records: list[WritebackAuditRecord] = []
    review_items: list[ReviewItem] = []
    evidence_output: dict[str, Any] = dict(evidence_map or {})
    evidence_fields = dict(evidence_output.get("fields") or {})
    evidence_output["fields"] = evidence_fields

    resolved: list[ResolvedPrediction] = []
    for prediction in predictions:
        add_evidence_record(evidence_fields, prediction, trace_by_field.get(prediction.field_id))
        target = resolve_target_cell(workbook, prediction.target_cell)
        if isinstance(target, str):
            audit_records.append(make_audit(prediction, action="invalid", reason=target, trace_id=trace_by_field.get(prediction.field_id)))
            review_items.append(make_review_item(prediction, reason=target, trace_id=trace_by_field.get(prediction.field_id)))
            continue
        resolved.append(ResolvedPrediction(prediction=prediction, target=target))

    duplicate_keys = {item.target.key for item in resolved if sum(1 for other in resolved if other.target.key == item.target.key) > 1}
    for item in resolved:
        prediction = item.prediction
        trace_id = trace_by_field.get(prediction.field_id)
        if item.target.key in duplicate_keys:
            audit_records.append(
                make_audit(
                    prediction,
                    action="conflict",
                    reason="duplicate_target_cell",
                    target=item.target,
                    trace_id=trace_id,
                )
            )
            review_items.append(make_review_item(prediction, reason="duplicate_target_cell", trace_id=trace_id))
            continue

        worksheet = workbook[item.target.sheet_name]
        cell = worksheet[item.target.cell]
        audit_record, review_item = apply_prediction(
            cell=cell,
            prediction=prediction,
            target=item.target,
            mode=mode,
            write_comments=write_comments,
            trace_id=trace_id,
        )
        audit_records.append(audit_record)
        if review_item:
            review_items.append(review_item)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    audit_path = output_path.parent / "writeback_audit.jsonl"
    evidence_map_output_path = output_path.parent / "evidence_map.json"
    review_items_path = output_path.parent / "review_items.jsonl"
    write_jsonl(audit_path, [record.to_dict() for record in audit_records])
    write_json(evidence_map_output_path, evidence_output)
    write_jsonl(review_items_path, [item.to_dict() for item in review_items])
    return build_summary(
        output_path=output_path,
        audit_path=audit_path,
        evidence_map_path=evidence_map_output_path,
        review_items_path=review_items_path,
        audit_records=audit_records,
        review_items=review_items,
    )


def apply_prediction(
    *,
    cell: Cell | MergedCell,
    prediction: FieldPrediction,
    target: TargetCell,
    mode: Mode,
    write_comments: bool,
    trace_id: str | None,
) -> tuple[WritebackAuditRecord, ReviewItem | None]:
    if prediction.answer_status != "answered":
        maybe_write_comment(cell, prediction, write_comments=write_comments, trace_id=trace_id)
        return (
            make_audit(prediction, action="skipped", reason="skipped_status", target=target, trace_id=trace_id),
            make_review_item(prediction, reason="skipped_status", trace_id=trace_id),
        )

    if not validation_passed(prediction):
        maybe_write_comment(cell, prediction, write_comments=write_comments, trace_id=trace_id)
        return (
            make_audit(prediction, action="skipped", reason="validation_failed", target=target, trace_id=trace_id),
            make_review_item(prediction, reason="validation_failed", trace_id=trace_id),
        )

    if mode == "safe" and is_formula_cell(cell):
        return (
            make_audit(prediction, action="skipped", reason="skipped_formula", target=target, trace_id=trace_id),
            make_review_item(prediction, reason="skipped_formula", trace_id=trace_id),
        )

    if isinstance(cell, MergedCell):
        return (
            make_audit(prediction, action="invalid", reason="invalid_cell", target=target, trace_id=trace_id),
            make_review_item(prediction, reason="invalid_cell", trace_id=trace_id),
        )

    cell.value = prediction.answer_value
    maybe_write_comment(cell, prediction, write_comments=write_comments, trace_id=trace_id)
    return make_audit(prediction, action="written", reason="written", target=target, trace_id=trace_id), None


def maybe_write_comment(
    cell: Cell | MergedCell,
    prediction: FieldPrediction,
    *,
    write_comments: bool,
    trace_id: str | None,
) -> None:
    if not write_comments or isinstance(cell, MergedCell):
        return
    comment_text = build_prediction_comment(prediction, trace_id=trace_id)
    if cell.comment and cell.comment.text:
        comment_text = f"{cell.comment.text}\n\n{comment_text}"
    cell.comment = Comment(comment_text, COMMENT_AUTHOR)


def validation_passed(prediction: FieldPrediction) -> bool:
    validation = prediction.validation or {}
    if validation.get("validation_pass") is False or validation.get("passed") is False:
        return False
    if validation.get("validation_failed") or validation.get("evidence_missing"):
        return False
    for key in ("constraint_violations", "errors", "error_messages"):
        if validation.get(key):
            return False
    return True


def is_formula_cell(cell: Cell | MergedCell) -> bool:
    return isinstance(cell.value, str) and cell.value.startswith("=")


def resolve_target_cell(workbook: Workbook, target_cell: str | None) -> TargetCell | str:
    if not target_cell:
        return "invalid_cell"
    raw = str(target_cell).strip()
    if not raw:
        return "invalid_cell"

    if "!" in raw:
        sheet_name_raw, cell_ref = raw.rsplit("!", 1)
        sheet_name = unquote_sheet_name(sheet_name_raw)
    else:
        sheet_name = workbook.active.title
        cell_ref = raw

    if sheet_name not in workbook.sheetnames:
        return "invalid_cell"

    coordinate = normalize_coordinate(cell_ref)
    if not coordinate:
        return "invalid_cell"

    worksheet = workbook[sheet_name]
    coordinate = resolve_merged_coordinate(worksheet, coordinate)
    return TargetCell(sheet_name=sheet_name, cell=coordinate, key=f"{sheet_name}!{coordinate}")


def unquote_sheet_name(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def normalize_coordinate(value: str) -> str | None:
    try:
        column_letters, row = coordinate_from_string(value.strip())
        column = column_index_from_string(column_letters)
    except ValueError:
        return None
    if row < 1 or row > MAX_EXCEL_ROW or column < 1 or column > MAX_EXCEL_COLUMN:
        return None
    return f"{get_column_letter(column)}{row}"


def resolve_merged_coordinate(worksheet: Worksheet, coordinate: str) -> str:
    for merged_range in worksheet.merged_cells.ranges:
        if coordinate in merged_range:
            return f"{get_column_letter(merged_range.min_col)}{merged_range.min_row}"
    return coordinate


def make_audit(
    prediction: FieldPrediction,
    *,
    action: str,
    reason: str,
    target: TargetCell | None = None,
    trace_id: str | None = None,
) -> WritebackAuditRecord:
    return WritebackAuditRecord(
        field_id=prediction.field_id,
        row_index=prediction.row_index,
        target_cell=prediction.target_cell,
        sheet_name=target.sheet_name if target else None,
        cell=target.cell if target else None,
        action=action,
        reason=reason,
        answer_status=prediction.answer_status,
        confidence=prediction.confidence,
        answer_value=prediction.answer_value,
        source_chunk_ids=prediction.source_chunk_ids,
        evidence_attachment_ids=prediction.evidence_attachment_ids,
        trace_id=trace_id,
    )


def make_review_item(prediction: FieldPrediction, *, reason: str, trace_id: str | None = None) -> ReviewItem:
    return ReviewItem(
        field_id=prediction.field_id,
        row_index=prediction.row_index,
        target_cell=prediction.target_cell,
        reason=reason,
        answer_status=prediction.answer_status,
        confidence=prediction.confidence,
        answer_value=prediction.answer_value,
        source_chunk_ids=prediction.source_chunk_ids,
        evidence_attachment_ids=prediction.evidence_attachment_ids,
        trace_id=trace_id,
    )


def add_evidence_record(evidence_fields: dict[str, Any], prediction: FieldPrediction, trace_id: str | None) -> None:
    evidence_fields[prediction.field_id] = {
        "target_cell": prediction.target_cell,
        "answer_status": prediction.answer_status,
        "confidence": prediction.confidence,
        "source_chunk_ids": prediction.source_chunk_ids,
        "evidence_attachment_ids": prediction.evidence_attachment_ids,
        "trace_id": trace_id,
    }


def load_trace_index(trace_path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for record in read_jsonl(trace_path):
        field_id = record.get("field_id") or (record.get("prediction") or {}).get("field_id")
        trace_id = record.get("trace_id") or record.get("id") or record.get("run_id")
        if field_id and trace_id:
            output[str(field_id)] = str(trace_id)
    return output


def build_summary(
    *,
    output_path: Path,
    audit_path: Path,
    evidence_map_path: Path,
    review_items_path: Path,
    audit_records: list[WritebackAuditRecord],
    review_items: list[ReviewItem],
) -> WritebackSummary:
    return WritebackSummary(
        output_path=output_path,
        audit_path=audit_path,
        evidence_map_path=evidence_map_path,
        review_items_path=review_items_path,
        total_count=len(audit_records),
        written_count=sum(1 for record in audit_records if record.action == "written"),
        skipped_count=sum(1 for record in audit_records if record.action == "skipped"),
        conflict_count=sum(1 for record in audit_records if record.action == "conflict"),
        invalid_count=sum(1 for record in audit_records if record.reason == "invalid_cell"),
        formula_skipped_count=sum(1 for record in audit_records if record.reason == "skipped_formula"),
        review_count=len(review_items),
    )
