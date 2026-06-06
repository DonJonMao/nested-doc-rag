from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/07_agent_need_audit"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outputs_exist() -> None:
    assert (OUT_DIR / "agent_need_summary.json").exists()
    assert (OUT_DIR / "agent_need_cases.jsonl").exists()
    assert (OUT_DIR / "visualization.md").exists()
    assert (OUT_DIR / "details.md").exists()


def test_main_excel_rules_are_sufficient() -> None:
    summary = json.loads((OUT_DIR / "agent_need_summary.json").read_text(encoding="utf-8"))
    assert summary["main_excel_tables"]["total"] == 10
    assert summary["main_excel_tables"]["rule_ok"] == 10


def test_deterministic_backlog_is_detected() -> None:
    cases = read_jsonl(OUT_DIR / "agent_need_cases.jsonl")
    assert any(case["category"] == "embedded_object_unparsed" for case in cases)
    assert not any(case["category"] == "embedded_object_parent_mapping" for case in cases)


def test_agent_candidates_are_table_structure_only() -> None:
    cases = read_jsonl(OUT_DIR / "agent_need_cases.jsonl")
    for case in cases:
        if case["need_type"] == "agent_candidate":
            assert "table" in case["category"]


def test_details_include_requested_sections() -> None:
    summary = json.loads((OUT_DIR / "agent_need_summary.json").read_text(encoding="utf-8"))
    details = (OUT_DIR / "details.md").read_text(encoding="utf-8")
    assert f"{summary['embedded_objects']['total']} 个嵌入对象" in details
    assert f"{summary['embedded_word_tables']['total']} 张嵌入 Word 表格" in details
    assert summary["embedded_word_tables"] == {
        "total": 188,
        "agent_candidate": 146,
        "rule_ok": 42,
    }
    assert "agent_candidate" in details
    assert "deterministic_backlog" in details


if __name__ == "__main__":
    test_outputs_exist()
    test_main_excel_rules_are_sufficient()
    test_deterministic_backlog_is_detected()
    test_agent_candidates_are_table_structure_only()
    test_details_include_requested_sections()
    print("step 07 agent need audit tests passed")
