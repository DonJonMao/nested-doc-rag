from __future__ import annotations

import json
import math
from pathlib import Path

from nested_doc_rag.evaluation.field_metrics import (
    evaluate_fields,
    evaluate_fields_from_files,
    exact_match,
    field_semantic_match,
    validate_constraints,
)
from nested_doc_rag.io import write_jsonl
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction


def gold(field_id: str, expected_value: object, field_type: str, **kwargs: object) -> FieldGold:
    return FieldGold.from_dict(
        {
            "field_id": field_id,
            "row_index": kwargs.pop("row_index", 1),
            "target_cell": kwargs.pop("target_cell", "C1"),
            "question_text": kwargs.pop("question_text", "测试字段"),
            "expected_value": expected_value,
            "expected_status": kwargs.pop("expected_status", "answered"),
            "field_type": field_type,
            "required": kwargs.pop("required", True),
            "must_have_evidence": kwargs.pop("must_have_evidence", False),
            **kwargs,
        }
    )


def pred(field_id: str, answer_value: object, **kwargs: object) -> FieldPrediction:
    return FieldPrediction.from_dict(
        {
            "field_id": field_id,
            "row_index": kwargs.pop("row_index", 1),
            "target_cell": kwargs.pop("target_cell", "C1"),
            "answer_value": answer_value,
            "answer_status": kwargs.pop("answer_status", "answered"),
            "confidence": kwargs.pop("confidence", 0.91),
            "source_chunk_ids": kwargs.pop("source_chunk_ids", ["chunk_1"]),
            "evidence_attachment_ids": kwargs.pop("evidence_attachment_ids", []),
            "validation": kwargs.pop("validation", {}),
            **kwargs,
        }
    )


def test_exact_match_normalizes_whitespace() -> None:
    assert exact_match("西咸4号楼 301机房", "西咸4号楼   301机房")
    assert not exact_match("301", "302")


def test_text_semantic_match_with_alias() -> None:
    item = gold("field_text", "西咸四号楼301机房", "text", accepted_aliases=["西咸4号楼 301机房"])
    assert field_semantic_match(item, pred("field_text", "西咸4号楼301机房"))


def test_enum_semantic_match_and_constraint() -> None:
    item = gold(
        "field_enum",
        "满足",
        "enum",
        accepted_aliases=["是"],
        constraints={"enum_values": ["满足", "部分满足", "不满足"]},
    )
    assert field_semantic_match(item, pred("field_enum", "是"))
    assert validate_constraints(item, pred("field_enum", "不在枚举中")) == ["enum_not_allowed"]


def test_number_semantic_match_with_unit_normalization_and_range() -> None:
    item = gold("field_number", "2 kW", "number", constraints={"min": 1.0, "max": 3.0})
    assert field_semantic_match(item, pred("field_number", "2000 W"))
    assert validate_constraints(item, pred("field_number", "4 kW")) == ["number_above_max"]


def test_date_semantic_match_and_invalid_date() -> None:
    item = gold("field_date", "2025-07-01", "date")
    assert field_semantic_match(item, pred("field_date", "2025年7月1日"))
    assert validate_constraints(item, pred("field_date", "2025-99-99")) == ["date_invalid"]


def test_bool_semantic_match_and_invalid_bool() -> None:
    item = gold("field_bool", "是", "bool")
    assert field_semantic_match(item, pred("field_bool", "true"))
    assert validate_constraints(item, pred("field_bool", "可能")) == ["bool_invalid"]


def test_field_evaluation_metrics_and_badcases() -> None:
    golds = [
        gold("ok_text", "A", "text", gold_source_chunk_ids=["chunk_1"], must_have_evidence=True),
        gold("bad_status", "B", "text"),
        gold("good_abstain", "", "text", expected_status="not_found", required=False),
    ]
    predictions = [
        pred("ok_text", "A", source_chunk_ids=["chunk_1"]),
        pred("bad_status", "C", answer_status="answered", source_chunk_ids=[]),
        pred("good_abstain", "", answer_status="not_found", source_chunk_ids=[]),
    ]

    result = evaluate_fields(golds, predictions)

    assert result.metrics["field_count"] == 3
    assert math.isclose(result.metrics["field_exact_match"], 2 / 3, rel_tol=1e-6)
    assert math.isclose(result.metrics["field_semantic_match"], 2 / 3, rel_tol=1e-6)
    assert result.metrics["abstention_precision"] == 1.0
    assert any("semantic_mismatch" in item["badcase_categories"] for item in result.badcases)


def test_evaluate_fields_from_files_writes_reports(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    out_dir = tmp_path / "out"
    write_jsonl(
        gold_path,
        [
            gold("field_001", "2 kW", "number", gold_source_chunk_ids=["chunk_power"], must_have_evidence=True).to_dict(),
            gold("field_002", "满足", "enum", constraints={"enum_values": ["满足", "不满足"]}).to_dict(),
        ],
    )
    write_jsonl(
        pred_path,
        [
            pred("field_001", "2000 W", source_chunk_ids=["chunk_power"]).to_dict(),
            pred("field_002", "不满足", source_chunk_ids=["chunk_status"]).to_dict(),
        ],
    )

    result = evaluate_fields_from_files(gold_path=gold_path, pred_path=pred_path, out_dir=out_dir)

    assert result.metrics["field_count"] == 2
    report_json = json.loads((out_dir / "field_eval_report.json").read_text(encoding="utf-8"))
    report_md = (out_dir / "field_eval_report.md").read_text(encoding="utf-8")
    assert report_json["metrics"]["field_count"] == 2
    assert "## 整体指标" in report_md
    assert "## Badcase 分类" in report_md
    assert (out_dir / "field_eval_rows.csv").exists()
    assert (out_dir / "badcases.jsonl").exists()
