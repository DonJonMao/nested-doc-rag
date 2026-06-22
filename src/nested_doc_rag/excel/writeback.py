from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
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
WRITEBACK_STATUSES = {"confirmed", "uncertain", "flagged"}
WRITEBACK_ACTIONS = {
    "written",
    "written_red_comment",
    "review_only",
    "skipped_uncertain_policy",
    "skipped_non_empty_cell",
    "skipped_formula",
    "invalid_cell",
    "duplicate_target_cell",
}
WB_INVALID_CELL = "WB_INVALID_CELL"
WB_MISSING_EVIDENCE = "WB_MISSING_EVIDENCE"
WB_POLICY_REJECTED = "WB_POLICY_REJECTED"
WB_COMMENT_TOO_LONG = "WB_COMMENT_TOO_LONG"
CRITICAL_FLAGS = {
    "answered_without_source",
    "invalid_source_reference",
    "answered_from_global_intro_risk",
    "answer_too_long",
    "scope_mismatch_risk",
    "liquid_cooling_scope_mismatch",
    "field_intent_source_mismatch",
}


@dataclass(frozen=True)
class TargetCell:
    sheet_name: str
    cell: str
    key: str


@dataclass(frozen=True)
class ResolvedPrediction:
    prediction: FieldPrediction
    target: TargetCell
    status: str
    evidence_refs: list[dict[str, Any]]


@dataclass(frozen=True)
class WritebackPolicy:
    allow_uncertain: bool = False
    uncertain_style: str = "red_fill"
    uncertain_comment_prefix: str = "[UNCERTAIN]"
    embed_evidence_images: bool = False
    evidence_image_mode: str = "hyperlink"
    max_evidence_images_per_field: int = 3
    max_comment_chars: int = 2000

    @classmethod
    def from_value(cls, value: Any | None) -> WritebackPolicy:
        if value is None:
            return cls()
        if isinstance(value, Mapping):
            getter = value.get
        else:
            getter = lambda name, default=None: getattr(value, name, default)
        return cls(
            allow_uncertain=bool(getter("allow_uncertain", False)),
            uncertain_style=str(getter("uncertain_style", "red_fill")),
            uncertain_comment_prefix=str(getter("uncertain_comment_prefix", "[UNCERTAIN]")),
            embed_evidence_images=bool(getter("embed_evidence_images", False)),
            evidence_image_mode=str(getter("evidence_image_mode", "hyperlink")),
            max_evidence_images_per_field=int(getter("max_evidence_images_per_field", 3) or 3),
            max_comment_chars=int(getter("max_comment_chars", 2000) or 2000),
        )


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
    overlays_by_field_id: Mapping[str, Any] | None = None,
    writeback_config: Any | None = None,
    run_id: str | None = None,
) -> WritebackSummary:
    if mode not in {"safe", "overwrite"}:
        raise ValueError("mode must be 'safe' or 'overwrite'")

    workbook = load_workbook(template_path)
    trace_by_field = trace_by_field or {}
    overlays_by_field_id = overlays_by_field_id or {}
    policy = WritebackPolicy.from_value(writeback_config)
    audit_records: list[WritebackAuditRecord] = []
    review_items: list[ReviewItem] = []
    image_evidence_records: list[dict[str, Any]] = []
    evidence_output: dict[str, Any] = dict(evidence_map or {})
    evidence_fields = dict(evidence_output.get("fields") or {})
    evidence_output["fields"] = evidence_fields

    resolved: list[ResolvedPrediction] = []
    for prediction in predictions:
        overlay = overlays_by_field_id.get(prediction.field_id)
        status = classify_writeback_status(prediction, overlay)
        evidence_refs = evidence_refs_for_prediction(
            prediction,
            overlay,
            run_id=run_id,
            max_image_refs=policy.max_evidence_images_per_field,
        )
        image_evidence_records.extend(image_evidence_from_refs(prediction.field_id, evidence_refs))
        add_evidence_record(evidence_fields, prediction, trace_by_field.get(prediction.field_id), status=status, evidence_refs=evidence_refs)
        target = resolve_target_cell(workbook, prediction.target_cell)
        if isinstance(target, str):
            audit_records.append(
                make_audit(
                    prediction,
                    action="invalid",
                    reason=target,
                    trace_id=trace_by_field.get(prediction.field_id),
                    status="flagged",
                    writeback_action="invalid_cell",
                    evidence_refs=evidence_refs,
                    error_code=WB_INVALID_CELL,
                )
            )
            review_items.append(
                make_review_item(
                    prediction,
                    reason=target,
                    trace_id=trace_by_field.get(prediction.field_id),
                    status="flagged",
                    writeback_action="invalid_cell",
                    evidence_refs=evidence_refs,
                    error_code=WB_INVALID_CELL,
                )
            )
            continue
        resolved.append(ResolvedPrediction(prediction=prediction, target=target, status=status, evidence_refs=evidence_refs))

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
                    status="flagged",
                    writeback_action="duplicate_target_cell",
                    evidence_refs=item.evidence_refs,
                    error_code=WB_POLICY_REJECTED,
                )
            )
            review_items.append(
                make_review_item(
                    prediction,
                    reason="duplicate_target_cell",
                    trace_id=trace_id,
                    status="flagged",
                    writeback_action="duplicate_target_cell",
                    evidence_refs=item.evidence_refs,
                    error_code=WB_POLICY_REJECTED,
                )
            )
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
            status=item.status,
            evidence_refs=item.evidence_refs,
            policy=policy,
        )
        audit_records.append(audit_record)
        if review_item:
            review_items.append(review_item)

    write_evidence_sheet(workbook, audit_records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    audit_path = output_path.parent / "writeback_audit.jsonl"
    evidence_map_output_path = output_path.parent / "evidence_map.json"
    review_items_path = output_path.parent / "review_items.jsonl"
    image_evidence_path = output_path.parent / "image_evidence.jsonl"
    write_jsonl(audit_path, [record.to_dict() for record in audit_records])
    write_json(evidence_map_output_path, evidence_output)
    write_jsonl(review_items_path, [item.to_dict() for item in review_items])
    if image_evidence_records:
        write_jsonl(image_evidence_path, image_evidence_records)
    return build_summary(
        output_path=output_path,
        audit_path=audit_path,
        evidence_map_path=evidence_map_output_path,
        review_items_path=review_items_path,
        image_evidence_path=image_evidence_path if image_evidence_records else None,
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
    status: str,
    evidence_refs: list[dict[str, Any]],
    policy: WritebackPolicy,
) -> tuple[WritebackAuditRecord, ReviewItem | None]:
    if status == "flagged":
        reason = flagged_reason(prediction)
        return (
            make_audit(
                prediction,
                action="skipped",
                reason=reason,
                target=target,
                trace_id=trace_id,
                status=status,
                writeback_action="review_only",
                evidence_refs=evidence_refs,
            ),
            make_review_item(
                prediction,
                reason=reason,
                trace_id=trace_id,
                status=status,
                writeback_action="review_only",
                evidence_refs=evidence_refs,
            ),
        )

    if status == "uncertain" and not evidence_refs:
        return (
            make_audit(
                prediction,
                action="skipped",
                reason="missing_evidence",
                target=target,
                trace_id=trace_id,
                status=status,
                writeback_action="review_only",
                evidence_refs=evidence_refs,
                error_code=WB_MISSING_EVIDENCE,
            ),
            make_review_item(
                prediction,
                reason="missing_evidence",
                trace_id=trace_id,
                status=status,
                writeback_action="review_only",
                evidence_refs=evidence_refs,
                error_code=WB_MISSING_EVIDENCE,
            ),
        )

    if mode == "safe" and is_formula_cell(cell):
        return (
            make_audit(
                prediction,
                action="skipped",
                reason="skipped_formula",
                target=target,
                trace_id=trace_id,
                status="flagged" if status == "confirmed" else status,
                writeback_action="skipped_formula",
                evidence_refs=evidence_refs,
                error_code=WB_POLICY_REJECTED,
            ),
            make_review_item(
                prediction,
                reason="skipped_formula",
                trace_id=trace_id,
                status="flagged" if status == "confirmed" else status,
                writeback_action="skipped_formula",
                evidence_refs=evidence_refs,
                error_code=WB_POLICY_REJECTED,
            ),
        )

    if isinstance(cell, MergedCell):
        return (
            make_audit(
                prediction,
                action="invalid",
                reason="invalid_cell",
                target=target,
                trace_id=trace_id,
                status="flagged" if status == "confirmed" else status,
                writeback_action="invalid_cell",
                evidence_refs=evidence_refs,
                error_code=WB_INVALID_CELL,
            ),
            make_review_item(
                prediction,
                reason="invalid_cell",
                trace_id=trace_id,
                status="flagged" if status == "confirmed" else status,
                writeback_action="invalid_cell",
                evidence_refs=evidence_refs,
                error_code=WB_INVALID_CELL,
            ),
        )

    if status == "uncertain":
        if not policy.allow_uncertain:
            return (
                make_audit(
                    prediction,
                    action="skipped",
                    reason="uncertain_writeback_disabled",
                    target=target,
                    trace_id=trace_id,
                    status=status,
                    writeback_action="skipped_uncertain_policy",
                    evidence_refs=evidence_refs,
                    error_code=WB_POLICY_REJECTED,
                ),
                make_review_item(
                    prediction,
                    reason="uncertain_writeback_disabled",
                    trace_id=trace_id,
                    status=status,
                    writeback_action="skipped_uncertain_policy",
                    evidence_refs=evidence_refs,
                    error_code=WB_POLICY_REJECTED,
                ),
            )
        if not is_empty_cell(cell):
            return (
                make_audit(
                    prediction,
                    action="skipped",
                    reason="uncertain_target_not_empty",
                    target=target,
                    trace_id=trace_id,
                    status=status,
                    writeback_action="skipped_non_empty_cell",
                    evidence_refs=evidence_refs,
                    error_code=WB_POLICY_REJECTED,
                ),
                make_review_item(
                    prediction,
                    reason="uncertain_target_not_empty",
                    trace_id=trace_id,
                    status=status,
                    writeback_action="skipped_non_empty_cell",
                    evidence_refs=evidence_refs,
                    error_code=WB_POLICY_REJECTED,
                ),
            )
        cell.value = prediction.answer_value
        apply_uncertain_style(cell, policy)
        comment_length, comment_truncated = maybe_write_uncertain_comment(
            cell,
            prediction,
            evidence_refs,
            write_comments=write_comments,
            trace_id=trace_id,
            policy=policy,
        )
        return (
            make_audit(
                prediction,
                action="written",
                reason="written_uncertain",
                target=target,
                trace_id=trace_id,
                status=status,
                writeback_action="written_red_comment",
                evidence_refs=evidence_refs,
                error_code=WB_COMMENT_TOO_LONG if comment_truncated else None,
                comment_length=comment_length,
            ),
            make_review_item(
                prediction,
                reason="uncertain_written",
                trace_id=trace_id,
                status=status,
                writeback_action="written_red_comment",
                evidence_refs=evidence_refs,
            ),
        )

    cell.value = prediction.answer_value
    maybe_write_comment(cell, prediction, write_comments=write_comments, trace_id=trace_id)
    return (
        make_audit(
            prediction,
            action="written",
            reason="written",
            target=target,
            trace_id=trace_id,
            status="confirmed",
            writeback_action="written",
            evidence_refs=evidence_refs,
        ),
        None,
    )


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


def maybe_write_uncertain_comment(
    cell: Cell | MergedCell,
    prediction: FieldPrediction,
    evidence_refs: list[dict[str, Any]],
    *,
    write_comments: bool,
    trace_id: str | None,
    policy: WritebackPolicy,
) -> tuple[int, bool]:
    if not write_comments or isinstance(cell, MergedCell):
        return 0, False
    comment_text = build_uncertain_comment(prediction, evidence_refs, trace_id=trace_id, policy=policy)
    if cell.comment and cell.comment.text:
        comment_text = f"{cell.comment.text}\n\n{comment_text}"
    truncated = False
    if len(comment_text) > policy.max_comment_chars:
        suffix = f"\n{WB_COMMENT_TOO_LONG}: comment truncated"
        comment_text = comment_text[: max(0, policy.max_comment_chars - len(suffix))].rstrip() + suffix
        truncated = True
    cell.comment = Comment(comment_text, COMMENT_AUTHOR)
    return len(comment_text), truncated


def build_uncertain_comment(
    prediction: FieldPrediction,
    evidence_refs: list[dict[str, Any]],
    *,
    trace_id: str | None,
    policy: WritebackPolicy,
) -> str:
    lines = [
        policy.uncertain_comment_prefix,
        f"field_id: {prediction.field_id}",
        f"answer_status: {prediction.answer_status}",
        f"answer_value: {display_comment_value(prediction.answer_value)}",
    ]
    if trace_id:
        lines.append(f"trace_id: {trace_id}")
    lines.append("evidence:")
    for index, ref in enumerate(evidence_refs[:5], start=1):
        location = ref_location(ref)
        source = ref.get("file_name") or ref.get("document_id") or ref.get("object_key") or ref.get("chunk_id") or "unknown"
        lines.append(f"{index}. source={source}; location={location}")
        preview = display_comment_value(ref.get("text_preview") or ref.get("caption") or "")
        if preview:
            lines.append(f"   summary={preview}")
        if ref.get("image_object_key"):
            lines.append(f"   image={ref.get('image_object_key')}")
    return "\n".join(lines)


def display_comment_value(value: Any, max_chars: int = 180) -> str:
    text = "" if value is None else str(value).strip()
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def ref_location(ref: Mapping[str, Any]) -> str:
    parts = [
        ref.get("source_anchor"),
        f"page {ref.get('page')}" if ref.get("page") is not None else None,
        ref.get("sheet_name"),
        ref.get("cell"),
    ]
    return " / ".join(str(part) for part in parts if part) or "unknown"


def apply_uncertain_style(cell: Cell, policy: WritebackPolicy) -> None:
    if policy.uncertain_style == "red_font":
        cell.font = Font(
            name=cell.font.name,
            sz=cell.font.sz,
            bold=cell.font.bold,
            italic=cell.font.italic,
            color="FFFF0000",
        )
        return
    cell.fill = PatternFill(fill_type="solid", fgColor="FFFFCCCC")


def is_empty_cell(cell: Cell | MergedCell) -> bool:
    return cell.value is None or str(cell.value).strip() == ""


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


def flagged_reason(prediction: FieldPrediction) -> str:
    if prediction.answer_status != "answered":
        return "skipped_status"
    if not validation_passed(prediction):
        return "validation_failed"
    return "review_only"


def classify_writeback_status(prediction: FieldPrediction, overlay: Any | None) -> str:
    writeback_allowed = bool(overlay_value(overlay, "writeback_allowed", False))
    critic_flags = {str(flag) for flag in overlay_value(overlay, "critic_flags", []) or []}
    if overlay is None and prediction.answer_status == "answered" and validation_passed(prediction):
        return "confirmed"
    if prediction.answer_status == "answered" and validation_passed(prediction) and writeback_allowed:
        return "confirmed"
    if has_usable_answer(prediction) and has_evidence(prediction, overlay) and not critic_flags.intersection(CRITICAL_FLAGS):
        return "uncertain"
    return "flagged"


def has_usable_answer(prediction: FieldPrediction) -> bool:
    if prediction.answer_status not in {"answered", "partial_clue"}:
        return False
    text = display_comment_value(prediction.answer_value, max_chars=400)
    if not text:
        return False
    blocked_prefixes = ("未找到", "无可直接填写", "检索到相关线索，但证据不足")
    return not any(text.startswith(prefix) for prefix in blocked_prefixes)


def has_evidence(prediction: FieldPrediction, overlay: Any | None) -> bool:
    return bool(
        prediction.source_chunk_ids
        or prediction.reference_chunk_ids
        or prediction.reference_source_documents
        or prediction.evidence_attachment_ids
        or overlay_value(overlay, "suggested_reference_source_documents", [])
        or overlay_value(overlay, "suggested_reference_chunk_ids", [])
    )


def overlay_value(overlay: Any | None, name: str, default: Any = None) -> Any:
    if overlay is None:
        return default
    if isinstance(overlay, Mapping):
        return overlay.get(name, default)
    return getattr(overlay, name, default)


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
    status: str = "flagged",
    writeback_action: str = "review_only",
    evidence_refs: list[dict[str, Any]] | None = None,
    error_code: str | None = None,
    comment_length: int = 0,
) -> WritebackAuditRecord:
    evidence_refs = evidence_refs or []
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
        status=status,
        writeback_action=writeback_action,
        evidence_refs=evidence_refs,
        evidence_count=len(evidence_refs),
        image_evidence_count=sum(1 for ref in evidence_refs if ref.get("image_object_key")),
        error_code=error_code,
        comment_length=comment_length,
    )


def make_review_item(
    prediction: FieldPrediction,
    *,
    reason: str,
    trace_id: str | None = None,
    status: str = "flagged",
    writeback_action: str = "review_only",
    evidence_refs: list[dict[str, Any]] | None = None,
    error_code: str | None = None,
) -> ReviewItem:
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
        status=status,
        writeback_action=writeback_action,
        evidence_refs=evidence_refs or [],
        error_code=error_code,
    )


def add_evidence_record(
    evidence_fields: dict[str, Any],
    prediction: FieldPrediction,
    trace_id: str | None,
    *,
    status: str,
    evidence_refs: list[dict[str, Any]],
) -> None:
    evidence_fields[prediction.field_id] = {
        "target_cell": prediction.target_cell,
        "answer_status": prediction.answer_status,
        "confidence": prediction.confidence,
        "source_chunk_ids": prediction.source_chunk_ids,
        "evidence_attachment_ids": prediction.evidence_attachment_ids,
        "status": status,
        "evidence_refs": evidence_refs,
        "trace_id": trace_id,
    }


def evidence_refs_for_prediction(
    prediction: FieldPrediction,
    overlay: Any | None,
    *,
    run_id: str | None,
    max_image_refs: int,
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    docs.extend(dict(item) for item in prediction.reference_source_documents if isinstance(item, dict))
    docs.extend(dict(item) for item in overlay_value(overlay, "suggested_reference_source_documents", []) or [] if isinstance(item, dict))
    if not docs:
        docs.extend({"chunk_id": chunk_id} for chunk_id in prediction.source_chunk_ids)

    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    image_count = 0
    for doc in docs:
        ref = normalize_evidence_ref(doc)
        key = "|".join(str(ref.get(part) or "") for part in ("chunk_id", "source_anchor", "object_key", "qdrant_point_id"))
        if key in seen:
            continue
        seen.add(key)
        proof_ids = [str(item) for item in doc.get("proof_attachment_ids") or [] if item]
        if not ref.get("image_object_key") and proof_ids and image_count < max_image_refs:
            ref["image_object_key"] = image_object_key_for(run_id, prediction.field_id, proof_ids[0])
            ref["caption"] = ref.get("caption") or "proof attachment"
            image_count += 1
        refs.append(ref)

    if prediction.evidence_attachment_ids:
        for attachment_id in prediction.evidence_attachment_ids:
            if image_count >= max_image_refs:
                break
            object_key = image_object_key_for(run_id, prediction.field_id, attachment_id)
            if any(ref.get("image_object_key") == object_key for ref in refs):
                continue
            refs.append(
                {
                    "chunk_id": "",
                    "document_id": "",
                    "object_key": "",
                    "qdrant_point_id": "",
                    "source_type": "attachment",
                    "source_anchor": "",
                    "page": None,
                    "sheet_name": "",
                    "cell": "",
                    "image_object_key": object_key,
                    "bbox": [],
                    "caption": "evidence attachment",
                }
            )
            image_count += 1
    return refs


def normalize_evidence_ref(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": str(doc.get("chunk_id") or ""),
        "document_id": doc.get("document_id") or doc.get("doc_id") or doc.get("file_id") or "",
        "object_key": doc.get("object_key") or doc.get("source_object_key") or "",
        "object_version_id": doc.get("object_version_id") or "",
        "qdrant_point_id": doc.get("qdrant_point_id") or doc.get("point_id") or "",
        "source_type": doc.get("source_type") or "",
        "source_anchor": doc.get("source_anchor") or doc.get("anchor") or "",
        "page": doc.get("page"),
        "sheet_name": doc.get("sheet_name") or "",
        "cell": doc.get("cell") or doc.get("cell_range") or "",
        "image_object_key": doc.get("image_object_key") or "",
        "bbox": doc.get("bbox") or [],
        "caption": doc.get("caption") or "",
        "file_name": doc.get("file_name") or "",
        "text_preview": doc.get("text_preview") or "",
    }


def image_object_key_for(run_id: str | None, field_id: str, attachment_id: str) -> str:
    safe_run = safe_object_key_part(run_id or "local_run")
    safe_field = safe_object_key_part(field_id)
    safe_attachment = safe_object_key_part(attachment_id)
    return f"runs/{safe_run}/evidence/{safe_field}/{safe_attachment}"


def safe_object_key_part(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.=-]+", "_", str(value or "").strip())
    return text.strip("._") or "unknown"


def image_evidence_from_refs(field_id: str, evidence_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for ref in evidence_refs:
        image_object_key = ref.get("image_object_key")
        if not image_object_key:
            continue
        records.append(
            {
                "field_id": field_id,
                "image_object_key": image_object_key,
                "document_id": ref.get("document_id") or "",
                "source_anchor": ref.get("source_anchor") or "",
                "caption": ref.get("caption") or "",
            }
        )
    return records


def write_evidence_sheet(workbook: Workbook, audit_records: list[WritebackAuditRecord]) -> None:
    rows: list[list[Any]] = []
    for record in audit_records:
        for ref in record.evidence_refs:
            rows.append(
                [
                    record.field_id,
                    record.status,
                    record.answer_value,
                    ref.get("file_name") or ref.get("document_id") or ref.get("object_key"),
                    ref_location(ref),
                    ref.get("text_preview") or ref.get("caption"),
                    ref.get("image_object_key") or "",
                ]
            )
    if not rows:
        return
    if "Evidence" in workbook.sheetnames:
        del workbook["Evidence"]
    sheet = workbook.create_sheet("Evidence")
    headers = ["field_id", "status", "answer_value", "source", "location", "text_preview", "image_object_key"]
    sheet.append(headers)
    for row in rows:
        sheet.append(row)


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
    image_evidence_path: Path | None,
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
        confirmed_count=sum(1 for record in audit_records if record.status == "confirmed"),
        uncertain_count=sum(1 for record in audit_records if record.status == "uncertain"),
        flagged_count=sum(1 for record in audit_records if record.status == "flagged"),
        image_evidence_path=image_evidence_path,
        fields=[manifest_field_from_audit(record) for record in audit_records],
    )


def manifest_field_from_audit(record: WritebackAuditRecord) -> dict[str, Any]:
    return {
        "field_key": record.field_id,
        "field_id": record.field_id,
        "row_index": record.row_index,
        "target_cell": record.target_cell,
        "sheet_name": record.sheet_name,
        "cell": record.cell,
        "status": record.status,
        "answer_status": record.answer_status,
        "answer_value": record.answer_value,
        "writeback_action": record.writeback_action,
        "evidence_refs": record.evidence_refs,
        "error_code": record.error_code,
    }
