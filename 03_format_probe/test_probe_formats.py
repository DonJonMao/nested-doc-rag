from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/03_format_probe"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def by_relative_path(records: list[dict]) -> dict[str, dict]:
    return {record["relative_path"]: record for record in records}


def test_outputs_exist() -> None:
    assert (OUT_DIR / "probed_manifest.jsonl").exists()
    assert (OUT_DIR / "summary.json").exists()
    assert (OUT_DIR / "visualization.md").exists()


def test_parser_type_counts() -> None:
    records = read_jsonl(OUT_DIR / "probed_manifest.jsonl")
    parser_counts = {}
    for record in records:
        parser_counts[record["parser_type"]] = parser_counts.get(record["parser_type"], 0) + 1
    assert parser_counts["xlsx_ooxml"] == 15
    assert parser_counts["docx_ooxml"] == 4


def test_updated_xixian_6_is_parseable() -> None:
    records = by_relative_path(read_jsonl(OUT_DIR / "probed_manifest.jsonl"))
    updated = records["西咸数据中心6号楼维护能力知识库.xlsx"]
    assert updated["parse_status"] == "ok"
    assert updated["parser_type"] == "xlsx_ooxml"
    assert updated["fallback_action"] is None
    assert updated["probe"]["has_workbook"] is True


if __name__ == "__main__":
    test_outputs_exist()
    test_parser_type_counts()
    test_updated_xixian_6_is_parseable()
    print("step 03 tests passed")
