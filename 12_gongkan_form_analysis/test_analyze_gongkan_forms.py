from __future__ import annotations

import json
from pathlib import Path

from analyze_gongkan_forms import DEFAULT_OUT_DIR, run


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outputs_exist() -> None:
    assert (DEFAULT_OUT_DIR / "sheet_profiles.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "agent_sheet_hints.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "form_items.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "rag_return_contract.json").exists()
    assert (DEFAULT_OUT_DIR / "summary.json").exists()
    assert (DEFAULT_OUT_DIR / "visualization.md").exists()


def test_summary_counts() -> None:
    summary = json.loads((DEFAULT_OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["survey_file_count"] == 5
    assert summary["sheet_count"] == 16
    assert summary["form_item_count"] > 500
    assert summary["items_needing_evidence"] > 20
    assert summary["agent_hint_count"] == 16


def test_sheet_profiles_cover_known_shapes() -> None:
    profiles = read_jsonl(DEFAULT_OUT_DIR / "sheet_profiles.jsonl")
    by_sheet = {(item["file_name"], item["sheet_name"]): item for item in profiles}
    assert by_sheet[("CDN机房工勘调研.xlsx", "机房工勘表")]["sheet_kind"] == "row_question_answer_form"
    assert by_sheet[("工堪表-v2.32（20240612）.xlsx", "机柜摆放平面图")]["sheet_kind"] == "evidence_or_layout_sheet"
    assert by_sheet[("陕西西安移动三线CDN机房排查表（2025.07）.xlsx", "配电系统")]["sheet_kind"] == "assessment_matrix"
    assert by_sheet[("陕西西安移动三线CDN机房排查表（2025.07）.xlsx", "机房风险统计")]["sheet_kind"] == "risk_register"


def test_form_items_have_rag_contract() -> None:
    items = read_jsonl(DEFAULT_OUT_DIR / "form_items.jsonl")
    for item in items[:100]:
        assert item["form_item_id"].startswith("gkitem_")
        assert item["question_text"] or item["existing_value"] or item["current_info"]
        assert item["rag_return_format"]["mode"]
        assert item["suggested_retrieval_query"] is not None


def test_agent_hints_are_structure_only() -> None:
    hints = read_jsonl(DEFAULT_OUT_DIR / "agent_sheet_hints.jsonl")
    assert len(hints) == 16
    for hint in hints:
        agent_hint = hint["agent_hint"]
        assert "sheet_kind" in agent_hint
        assert "rag_return_format" in agent_hint
        assert "answer_value" not in agent_hint


def test_rag_return_contract_modes() -> None:
    contract = json.loads((DEFAULT_OUT_DIR / "rag_return_contract.json").read_text(encoding="utf-8"))
    assert contract["contract_name"] == "gongkan_rag_answer_v1"
    assert set(contract["modes"]) == {"cell_answer", "status_with_current_info", "finding_row", "risk_row"}
    assert "source_chunks" in contract["common_fields"]


if __name__ == "__main__":
    if not (DEFAULT_OUT_DIR / "summary.json").exists():
        run()
    test_outputs_exist()
    test_summary_counts()
    test_sheet_profiles_cover_known_shapes()
    test_form_items_have_rag_contract()
    test_agent_hints_are_structure_only()
    print("step 12 gongkan form analysis tests passed")
