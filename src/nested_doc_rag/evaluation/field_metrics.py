from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from nested_doc_rag.io import read_jsonl, write_json, write_jsonl
from nested_doc_rag.schemas.eval import FieldGold, FieldMetricRow, FieldPrediction

ANSWERED = "answered"
ABSTAIN_STATUSES = {"not_found", "partial_clue"}
NON_DIRECT_STATUSES = {"not_found", "partial_clue", "conflict_unresolved"}
BOOL_TRUE = {"1", "true", "yes", "y", "是", "有", "支持", "满足", "可提供", "能", "可以", "已配置"}
BOOL_FALSE = {"0", "false", "no", "n", "否", "无", "不支持", "不满足", "无法提供", "不能", "不可以", "未配置"}


@dataclass(frozen=True)
class FieldEvaluation:
    rows: list[FieldMetricRow]
    metrics: dict[str, Any]
    badcases: list[dict[str, Any]]


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text.casefold()


def normalize_enum(value: Any) -> str:
    text = normalize_text(value)
    return "".join(ch for ch in text if ch.isalnum() or ch in {"%", "℃", "°"})


def normalize_bool(value: Any) -> bool | None:
    text = normalize_enum(value)
    if text in {normalize_enum(item) for item in BOOL_TRUE}:
        return True
    if text in {normalize_enum(item) for item in BOOL_FALSE}:
        return False
    return None


def parse_number(value: Any) -> tuple[float, str] | None:
    text = normalize_text(value).replace(",", "")
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*([a-zA-Z\u4e00-\u9fff%℃°]*)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = normalize_enum(match.group(2))
    unit_multipliers = {
        "w": ("kw", 0.001),
        "kw": ("kw", 1.0),
        "mw": ("kw", 1000.0),
        "瓦": ("kw", 0.001),
        "千瓦": ("kw", 1.0),
        "kva": ("kva", 1.0),
        "mva": ("kva", 1000.0),
        "个": ("count", 1.0),
        "台": ("count", 1.0),
        "路": ("count", 1.0),
        "套": ("count", 1.0),
        "": ("", 1.0),
    }
    canonical_unit, multiplier = unit_multipliers.get(unit, (unit, 1.0))
    return number * multiplier, canonical_unit


def parse_date(value: Any) -> date | None:
    text = normalize_text(value)
    patterns = [
        r"(?P<year>\d{4})[-/.年](?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?",
        r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})",
    ]
    for pattern in patterns:
        match = re.fullmatch(pattern, text)
        if not match:
            continue
        try:
            return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        except ValueError:
            return None
    return None


def normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,，;；、/]+", str(value or ""))
    return sorted(normalize_enum(item) for item in items if normalize_enum(item))


def exact_match(expected: Any, actual: Any) -> bool:
    return normalize_text(expected) == normalize_text(actual)


def field_exact_match(gold: FieldGold, prediction: FieldPrediction) -> bool:
    return exact_match(gold.expected_value, prediction.answer_value)


def field_semantic_match(gold: FieldGold, prediction: FieldPrediction) -> bool:
    expected = gold.expected_value
    actual = prediction.answer_value
    if exact_match(expected, actual):
        return True
    if normalize_enum(actual) in {normalize_enum(item) for item in [expected, *gold.accepted_aliases]}:
        return True

    field_type = normalize_enum(gold.field_type)
    if field_type == "bool":
        return normalize_bool(expected) is not None and normalize_bool(expected) == normalize_bool(actual)
    if field_type == "number":
        expected_number = parse_number(expected)
        actual_number = parse_number(actual)
        if not expected_number or not actual_number:
            return False
        expected_value, expected_unit = expected_number
        actual_value, actual_unit = actual_number
        if expected_unit and actual_unit and expected_unit != actual_unit:
            return False
        return abs(expected_value - actual_value) <= max(1e-6, abs(expected_value) * 1e-6)
    if field_type == "date":
        return parse_date(expected) is not None and parse_date(expected) == parse_date(actual)
    if field_type == "enum":
        allowed = {normalize_enum(item) for item in gold.constraints.enum_values}
        expected_enum = normalize_enum(expected)
        actual_enum = normalize_enum(actual)
        return actual_enum == expected_enum or (bool(allowed) and expected_enum in allowed and actual_enum == expected_enum)
    if field_type == "list":
        return normalize_list(expected) == normalize_list(actual)
    return False


def validate_constraints(gold: FieldGold, prediction: FieldPrediction) -> list[str]:
    violations: list[str] = []
    value = prediction.answer_value
    normalized_value = normalize_text(value)
    field_type = normalize_enum(gold.field_type)

    if gold.required and (prediction.answer_status != ANSWERED or normalized_value == ""):
        violations.append("required_missing")
    if normalized_value == "":
        return violations

    if gold.constraints.regex and not re.search(gold.constraints.regex, str(value or "")):
        violations.append("regex_mismatch")
    if gold.constraints.enum_values:
        allowed = {normalize_enum(item) for item in [*gold.constraints.enum_values, *gold.accepted_aliases]}
        if normalize_enum(value) not in allowed:
            violations.append("enum_not_allowed")
    if field_type == "number" or gold.constraints.min is not None or gold.constraints.max is not None:
        parsed = parse_number(value)
        if not parsed:
            violations.append("number_invalid")
        else:
            number, _unit = parsed
            if gold.constraints.min is not None and number < gold.constraints.min:
                violations.append("number_below_min")
            if gold.constraints.max is not None and number > gold.constraints.max:
                violations.append("number_above_max")
    if field_type == "date" and parse_date(value) is None:
        violations.append("date_invalid")
    if field_type == "bool" and normalize_bool(value) is None:
        violations.append("bool_invalid")
    return violations


def answer_status_accuracy(gold: FieldGold, prediction: FieldPrediction) -> bool:
    return normalize_enum(gold.expected_status) == normalize_enum(prediction.answer_status)


def evidence_recall_at_k(gold_source_chunk_ids: list[str], source_chunk_ids: list[str], k: int) -> float | None:
    if not gold_source_chunk_ids:
        return None
    gold_set = set(gold_source_chunk_ids)
    predicted_set = set(source_chunk_ids[:k])
    return len(gold_set & predicted_set) / len(gold_set)


def is_abstention_correct(gold: FieldGold, prediction: FieldPrediction) -> bool | None:
    if prediction.answer_status not in ABSTAIN_STATUSES:
        return None
    return gold.expected_status in NON_DIRECT_STATUSES


def build_metric_row(
    gold: FieldGold,
    prediction: FieldPrediction,
    *,
    evidence_k: int = 5,
    human_review_threshold: float = 0.55,
) -> FieldMetricRow:
    exact = field_exact_match(gold, prediction)
    semantic = field_semantic_match(gold, prediction)
    status_match = answer_status_accuracy(gold, prediction)
    evidence_supported = prediction.answer_status == ANSWERED and bool(prediction.source_chunk_ids)
    recall = evidence_recall_at_k(gold.gold_source_chunk_ids, prediction.source_chunk_ids, evidence_k)
    abstention_correct = is_abstention_correct(gold, prediction)
    violations = validate_constraints(gold, prediction)

    badcase_categories: list[str] = []
    if not exact:
        badcase_categories.append("exact_mismatch")
    if gold.expected_status == ANSWERED and not semantic:
        badcase_categories.append("semantic_mismatch")
    if not status_match:
        badcase_categories.append("status_mismatch")
    if gold.must_have_evidence and prediction.answer_status == ANSWERED and not prediction.source_chunk_ids:
        badcase_categories.append("evidence_missing")
    if recall is not None and recall < 1:
        badcase_categories.append("evidence_low_recall")
    if abstention_correct is False:
        badcase_categories.append("abstention_error")
    if violations:
        badcase_categories.append("constraint_violation")

    needs_human_review = (
        bool(violations)
        or prediction.answer_status == "conflict_unresolved"
        or prediction.confidence < human_review_threshold
        or (gold.must_have_evidence and prediction.answer_status == ANSWERED and not prediction.source_chunk_ids)
        or (gold.expected_status == ANSWERED and not semantic)
    )
    if needs_human_review:
        badcase_categories.append("needs_human_review")

    correction_required = not status_match or (gold.expected_status == ANSWERED and not semantic) or (
        gold.expected_status != ANSWERED and prediction.answer_status == ANSWERED
    )
    if correction_required:
        badcase_categories.append("correction_required")

    return FieldMetricRow(
        field_id=gold.field_id,
        row_index=gold.row_index,
        target_cell=gold.target_cell,
        question_text=gold.question_text,
        field_type=gold.field_type,
        expected_value=gold.expected_value,
        answer_value=prediction.answer_value,
        expected_status=gold.expected_status,
        answer_status=prediction.answer_status,
        confidence=prediction.confidence,
        exact_match=exact,
        semantic_match=semantic,
        status_match=status_match,
        evidence_supported=evidence_supported,
        evidence_recall_at_k=recall,
        abstention_correct=abstention_correct,
        constraint_violations=violations,
        needs_human_review=needs_human_review,
        correction_required=correction_required,
        badcase_categories=sorted(set(badcase_categories)),
    )


def summarize_rows(rows: list[FieldMetricRow]) -> dict[str, Any]:
    total = len(rows)
    answered_rows = [row for row in rows if row.answer_status == ANSWERED]
    abstention_rows = [row for row in rows if row.abstention_correct is not None]
    evidence_recall_rows = [row for row in rows if row.evidence_recall_at_k is not None]
    return {
        "field_count": total,
        "field_exact_match": _rate(row.exact_match for row in rows),
        "field_semantic_match": _rate(row.semantic_match for row in rows),
        "answer_status_accuracy": _rate(row.status_match for row in rows),
        "evidence_support_rate": _rate(row.evidence_supported for row in answered_rows),
        "evidence_recall_at_k": _mean(row.evidence_recall_at_k for row in evidence_recall_rows),
        "abstention_precision": _rate(bool(row.abstention_correct) for row in abstention_rows),
        "constraint_violation_rate": _rate(bool(row.constraint_violations) for row in rows),
        "human_review_rate": _rate(row.needs_human_review for row in rows),
        "correction_required_rate": _rate(row.correction_required for row in rows),
    }


def evaluate_fields(
    golds: list[FieldGold],
    predictions: list[FieldPrediction],
    *,
    evidence_k: int = 5,
    human_review_threshold: float = 0.55,
) -> FieldEvaluation:
    predictions_by_id = {prediction.field_id: prediction for prediction in predictions}
    rows: list[FieldMetricRow] = []
    for gold in golds:
        prediction = predictions_by_id.get(gold.field_id) or FieldPrediction(
            field_id=gold.field_id,
            row_index=gold.row_index,
            target_cell=gold.target_cell,
            answer_value="",
            answer_status="not_found",
            confidence=0.0,
        )
        rows.append(build_metric_row(gold, prediction, evidence_k=evidence_k, human_review_threshold=human_review_threshold))
    badcases = [
        {
            **row.to_dict(),
            "badcase_reason": ", ".join(row.badcase_categories),
        }
        for row in rows
        if row.badcase_categories
    ]
    return FieldEvaluation(rows=rows, metrics=summarize_rows(rows), badcases=badcases)


def evaluate_fields_from_files(
    *,
    gold_path: Path,
    pred_path: Path,
    out_dir: Path,
    evidence_k: int = 5,
    human_review_threshold: float = 0.55,
) -> FieldEvaluation:
    golds = [FieldGold.from_dict(record) for record in read_jsonl(gold_path)]
    predictions = [FieldPrediction.from_dict(record) for record in read_jsonl(pred_path)]
    result = evaluate_fields(golds, predictions, evidence_k=evidence_k, human_review_threshold=human_review_threshold)
    write_field_eval_outputs(result, out_dir=out_dir, evidence_k=evidence_k)
    return result


def write_field_eval_outputs(result: FieldEvaluation, *, out_dir: Path, evidence_k: int = 5) -> None:
    from nested_doc_rag.evaluation.reports import render_field_eval_markdown

    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "metrics": result.metrics,
        "evidence_k": evidence_k,
        "field_count": len(result.rows),
        "badcase_count": len(result.badcases),
        "badcase_counts": badcase_counts(result.rows),
    }
    write_json(out_dir / "field_eval_report.json", report)
    (out_dir / "field_eval_report.md").write_text(render_field_eval_markdown(result, evidence_k=evidence_k), encoding="utf-8")
    write_jsonl(out_dir / "badcases.jsonl", result.badcases)
    write_field_rows_csv(out_dir / "field_eval_rows.csv", result.rows)


def write_field_rows_csv(path: Path, rows: list[FieldMetricRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "field_id",
        "row_index",
        "target_cell",
        "field_type",
        "expected_status",
        "answer_status",
        "expected_value",
        "answer_value",
        "confidence",
        "exact_match",
        "semantic_match",
        "status_match",
        "evidence_supported",
        "evidence_recall_at_k",
        "abstention_correct",
        "constraint_violations",
        "needs_human_review",
        "correction_required",
        "badcase_categories",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            value = row.to_dict()
            value["constraint_violations"] = ";".join(row.constraint_violations)
            value["badcase_categories"] = ";".join(row.badcase_categories)
            writer.writerow({field: value.get(field) for field in fields})


def badcase_counts(rows: list[FieldMetricRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for category in row.badcase_categories:
            counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def _rate(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(1 for item in items if item) / len(items), 6)


def _mean(values: Any) -> float:
    items = [float(item) for item in values if item is not None]
    if not items:
        return 0.0
    return round(sum(items) / len(items), 6)
