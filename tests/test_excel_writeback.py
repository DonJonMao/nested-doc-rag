from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from nested_doc_rag.excel.comments import format_source_comment
from nested_doc_rag.excel.writeback import patch_workbook, prepare_writeback_item
from nested_doc_rag.io import read_json, read_jsonl
from nested_doc_rag.schemas.eval import FieldPrediction


def make_prediction(
    field_id: str,
    target_cell: str,
    value: object,
    *,
    status: str = "answered",
    confidence: float = 0.91,
    validation: dict | None = None,
) -> FieldPrediction:
    return FieldPrediction(
        field_id=field_id,
        row_index=4,
        target_cell=target_cell,
        answer_value=value,
        answer_status=status,
        confidence=confidence,
        source_chunk_ids=["chunk_1"],
        evidence_attachment_ids=["img_1"],
        validation=validation or {},
    )


def make_template(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"
    worksheet["C4"] = "old answer"
    worksheet["C4"].fill = PatternFill(fill_type="solid", fgColor="FFFF00")
    worksheet["C4"].font = Font(bold=True, color="FF0000")
    worksheet["C4"].alignment = Alignment(horizontal="center")
    worksheet.column_dimensions["C"].width = 28
    worksheet.row_dimensions[4].height = 30
    worksheet["C5"] = "keep me"
    worksheet["D4"] = "=SUM(A1:A2)"
    worksheet["A1"] = 1
    worksheet["A2"] = 2
    worksheet.merge_cells("E1:F1")
    worksheet["E1"] = "merged"
    workbook.save(path)


def test_prepare_writeback_item() -> None:
    item = prepare_writeback_item("Sheet1", "A1", "未找到", comment="source missing")
    assert item.sheet_name == "Sheet1"
    assert item.cell == "A1"
    assert item.value == "未找到"
    assert item.comment == "source missing"


def test_format_source_comment() -> None:
    assert format_source_comment("chunk_1") == "chunk_1"
    assert format_source_comment("chunk_1", "low confidence") == "chunk_1\nlow confidence"


def test_write_answered_cell(tmp_path: Path) -> None:
    template = tmp_path / "template.xlsx"
    output = tmp_path / "filled_form.xlsx"
    make_template(template)

    summary = patch_workbook(template, [make_prediction("field_1", "C4", "new answer")], output)

    workbook = load_workbook(output)
    assert workbook["Sheet1"]["C4"].value == "new answer"
    assert summary.written_count == 1
    audit = read_jsonl(tmp_path / "writeback_audit.jsonl")
    assert audit[0]["action"] == "written"
    assert audit[0]["reason"] == "written"


def test_skip_not_found(tmp_path: Path) -> None:
    template = tmp_path / "template.xlsx"
    output = tmp_path / "filled_form.xlsx"
    make_template(template)

    summary = patch_workbook(template, [make_prediction("field_1", "C5", "未找到", status="not_found")], output)

    workbook = load_workbook(output)
    assert workbook["Sheet1"]["C5"].value == "keep me"
    assert summary.written_count == 0
    audit = read_jsonl(tmp_path / "writeback_audit.jsonl")
    review_items = read_jsonl(tmp_path / "review_items.jsonl")
    assert audit[0]["reason"] == "skipped_status"
    assert review_items[0]["reason"] == "skipped_status"


def test_preserve_style(tmp_path: Path) -> None:
    template = tmp_path / "template.xlsx"
    output = tmp_path / "filled_form.xlsx"
    make_template(template)

    patch_workbook(template, [make_prediction("field_1", "C4", "new answer")], output)

    workbook = load_workbook(output)
    worksheet = workbook["Sheet1"]
    cell = worksheet["C4"]
    assert cell.value == "new answer"
    assert cell.fill.fgColor.rgb == "00FFFF00"
    assert cell.font.bold is True
    assert cell.font.color.rgb == "00FF0000"
    assert cell.alignment.horizontal == "center"
    assert worksheet.column_dimensions["C"].width == 28
    assert worksheet.row_dimensions[4].height == 30
    assert "E1:F1" in {str(item) for item in worksheet.merged_cells.ranges}


def test_comment_contains_evidence(tmp_path: Path) -> None:
    template = tmp_path / "template.xlsx"
    output = tmp_path / "filled_form.xlsx"
    make_template(template)

    patch_workbook(
        template,
        [make_prediction("field_1", "C4", "new answer", confidence=0.91234)],
        output,
        trace_by_field={"field_1": "trace_001"},
    )

    workbook = load_workbook(output)
    comment = workbook["Sheet1"]["C4"].comment
    assert comment is not None
    assert "confidence: 0.9123" in comment.text
    assert "source_chunk_ids: chunk_1" in comment.text
    assert "evidence_attachment_ids: img_1" in comment.text
    assert "answer_status: answered" in comment.text
    assert "trace_id: trace_001" in comment.text
    evidence_map = read_json(tmp_path / "evidence_map.json")
    assert evidence_map["fields"]["field_1"]["source_chunk_ids"] == ["chunk_1"]


def test_skip_formula_cell(tmp_path: Path) -> None:
    template = tmp_path / "template.xlsx"
    output = tmp_path / "filled_form.xlsx"
    make_template(template)

    summary = patch_workbook(template, [make_prediction("field_1", "D4", "3")], output)

    workbook = load_workbook(output, data_only=False)
    assert workbook["Sheet1"]["D4"].value == "=SUM(A1:A2)"
    assert summary.formula_skipped_count == 1
    audit = read_jsonl(tmp_path / "writeback_audit.jsonl")
    assert audit[0]["reason"] == "skipped_formula"


def test_duplicate_target_cell_conflict(tmp_path: Path) -> None:
    template = tmp_path / "template.xlsx"
    output = tmp_path / "filled_form.xlsx"
    make_template(template)

    summary = patch_workbook(
        template,
        [
            make_prediction("field_1", "C4", "first"),
            make_prediction("field_2", "C4", "second"),
        ],
        output,
    )

    workbook = load_workbook(output)
    assert workbook["Sheet1"]["C4"].value == "old answer"
    assert summary.conflict_count == 2
    audit = read_jsonl(tmp_path / "writeback_audit.jsonl")
    assert {record["action"] for record in audit} == {"conflict"}
    assert {record["reason"] for record in audit} == {"duplicate_target_cell"}


def test_invalid_target_cell_audit(tmp_path: Path) -> None:
    template = tmp_path / "template.xlsx"
    output = tmp_path / "filled_form.xlsx"
    make_template(template)

    summary = patch_workbook(template, [make_prediction("field_1", "Missing!C4", "new answer")], output)

    workbook = load_workbook(output)
    assert workbook["Sheet1"]["C4"].value == "old answer"
    assert summary.invalid_count == 1
    audit = read_jsonl(tmp_path / "writeback_audit.jsonl")
    review_items = read_jsonl(tmp_path / "review_items.jsonl")
    assert audit[0]["action"] == "invalid"
    assert audit[0]["reason"] == "invalid_cell"
    assert review_items[0]["reason"] == "invalid_cell"
