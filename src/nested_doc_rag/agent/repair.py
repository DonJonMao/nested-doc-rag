from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from nested_doc_rag.evaluation.field_metrics import normalize_bool, normalize_enum, parse_number
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction

from .state import ValidationResult

UNCERTAIN_BOOL = {"可能", "待复核", "不确定", "未知", "待确认", "需确认"}


def repair_prediction_once(
    field: FieldGold,
    pred: FieldPrediction,
    validation: ValidationResult,
) -> tuple[FieldPrediction, dict[str, Any]]:
    before = pred.answer_value
    if pred.answer_status != "answered" or not pred.source_chunk_ids:
        return pred, repair_log("none", before, before, False, "non-answered or no-evidence prediction is not repairable")

    field_type = normalize_enum(field.field_type)
    if field_type == "enum":
        repaired_value = repair_enum_value(field, before)
        return repaired_result(pred, before, repaired_value, "enum_error", "canonicalized enum value")
    if field_type == "bool":
        repaired_value = repair_bool_value(before)
        if repaired_value is None:
            return pred, repair_log("format_error", before, before, False, "uncertain bool value requires human review")
        return repaired_result(pred, before, repaired_value, "format_error", "canonicalized bool value")
    if field_type == "number":
        repaired_value = repair_number_value(before)
        return repaired_result(pred, before, repaired_value, "format_error", "normalized number text")
    if field_type == "date":
        repaired_value = repair_date_value(before)
        return repaired_result(pred, before, repaired_value, "format_error", "normalized date text")
    if "answer_too_long" in validation.violations:
        repaired_value = str(before or "").strip()[:120]
        return repaired_result(pred, before, repaired_value, "answer_too_long", "truncated long answer")
    return pred, repair_log("none", before, before, False, "no repair policy for field type")


def repair_enum_value(field: FieldGold, value: Any) -> Any:
    normalized = normalize_enum(value)
    for enum_value in field.constraints.enum_values:
        enum_normalized = normalize_enum(enum_value)
        if normalized == enum_normalized or normalized in enum_normalized or enum_normalized in normalized:
            return enum_value
    if normalized in {normalize_enum(alias) for alias in field.accepted_aliases} and len(field.constraints.enum_values) == 1:
        return field.constraints.enum_values[0]
    return value


def repair_bool_value(value: Any) -> str | None:
    if normalize_enum(value) in {normalize_enum(item) for item in UNCERTAIN_BOOL}:
        return None
    normalized = normalize_bool(value)
    if normalized is True:
        return "是"
    if normalized is False:
        return "否"
    return None


def repair_number_value(value: Any) -> Any:
    parsed = parse_number(value)
    if not parsed:
        return value
    _number, _unit = parsed
    return value


def repair_date_value(value: Any) -> Any:
    text = str(value or "").strip()
    match = re.fullmatch(r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]?", text)
    if not match:
        return value
    return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"


def repaired_result(
    pred: FieldPrediction,
    before: Any,
    after: Any,
    repair_type: str,
    reason: str,
) -> tuple[FieldPrediction, dict[str, Any]]:
    success = after != before
    repaired = replace(
        pred,
        answer_value=after,
        validation={
            **pred.validation,
            "repair_attempted": True,
            "repair_type": repair_type,
            "repair_success": success,
        },
    )
    return repaired, repair_log(repair_type, before, after, success, reason if success else "no deterministic change was available")


def repair_log(repair_type: str, before: Any, after: Any, success: bool, reason: str) -> dict[str, Any]:
    return {
        "repair_type": repair_type,
        "before": before,
        "after": after,
        "success": success,
        "reason": reason,
    }
