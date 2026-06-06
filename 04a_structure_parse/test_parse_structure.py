from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/04a_structure_parse")


def load_report() -> dict:
    return json.loads((OUT_DIR / "parse_report.json").read_text(encoding="utf-8"))


def by_relative_path(report: dict) -> dict[str, dict]:
    return {record["relative_path"]: record for record in report["files"]}


def test_outputs_exist() -> None:
    assert (OUT_DIR / "files.jsonl").exists()
    assert (OUT_DIR / "parse_report.json").exists()
    assert (OUT_DIR / "visualization.md").exists()
    assert (OUT_DIR / "workbooks").exists()
    assert (OUT_DIR / "worksheets").exists()
    assert (OUT_DIR / "attachments").exists()


def test_parse_status_counts() -> None:
    report = load_report()
    assert report["total_files"] == 19
    assert report["parse_status_counts"]["ok"] == 19


def test_updated_xixian_6_is_parsed() -> None:
    records = by_relative_path(load_report())
    updated = records["西咸数据中心6号楼维护能力知识库.xlsx"]
    assert updated["parse_status"] == "ok"
    assert updated["parser_type"] == "xlsx_ooxml"
    assert updated["sheet_count"] == 1
    assert updated["total_cell_count"] > 900
    assert updated["total_dispimg_formula_count"] == 169


def test_dimension_a1_file_has_real_cells() -> None:
    records = by_relative_path(load_report())
    cdn = records["工勘单/陕西西安移动三线CDN机房排查表（2025.07）.xlsx"]
    assert cdn["parse_status"] == "ok"
    sheet_by_name = {sheet["sheet_name"]: sheet for sheet in cdn["sheets"]}
    assert sheet_by_name["配电系统"]["actual_dimension"] == "A1:T62"
    assert sheet_by_name["配电系统"]["non_empty_cell_count"] > 200


def test_dispimg_attachments_are_mapped() -> None:
    report = load_report()
    assert report["total_xlsx_dispimg_formulas"] > 1000
    assert report["total_attachments"] > 1000


def test_xlsx_ole_attachments_are_mapped_to_cells() -> None:
    attachments_path = OUT_DIR / "attachments" / "file_d203075f61.attachments.jsonl"
    attachments = [
        json.loads(line)
        for line in attachments_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    row_93 = [
        item
        for item in attachments
        if item.get("source_cell") == "E93" and item.get("attachment_type") == "embedded_object"
    ]
    assert len(row_93) == 1
    assert row_93[0]["anchor_type"] == "ole_object"
    assert row_93[0]["prog_id"] == "Package"
    assert row_93[0]["mapping_status"] == "mapped"


if __name__ == "__main__":
    test_outputs_exist()
    test_parse_status_counts()
    test_updated_xixian_6_is_parsed()
    test_dimension_a1_file_has_real_cells()
    test_dispimg_attachments_are_mapped()
    test_xlsx_ole_attachments_are_mapped_to_cells()
    print("step 04A tests passed")
