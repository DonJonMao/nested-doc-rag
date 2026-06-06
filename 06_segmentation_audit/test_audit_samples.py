from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/06_segmentation_audit"


def load_audit() -> dict:
    return json.loads((OUT_DIR / "sample_audit.json").read_text(encoding="utf-8"))


def test_outputs_exist() -> None:
    assert (OUT_DIR / "sample_audit.json").exists()
    assert (OUT_DIR / "excel_samples.jsonl").exists()
    assert (OUT_DIR / "word_samples.jsonl").exists()
    assert (OUT_DIR / "visualization.md").exists()


def test_selected_sample_counts() -> None:
    audit = load_audit()
    assert len(audit["excel_samples"]) == 3
    assert len(audit["word_samples"]) == 3


def test_excel_attachment_bindings_are_consistent() -> None:
    audit = load_audit()
    for item in audit["excel_samples"]:
        assert item["verdict"] == "pass"
        assert item["attachment_mismatches"] == []


def test_xixian_3_skipped_blank_row_is_visible() -> None:
    audit = load_audit()
    by_file = {item["file_name"]: item for item in audit["excel_samples"]}
    skipped = by_file["西咸数据中心3号楼维护能力知识库.xlsx"]["skipped_rows"]
    assert any(item["row_index"] == 6 and not item["capability_desc"] and not item["answer_value"] for item in skipped)


def test_word_media_directory_entries_are_filtered() -> None:
    audit = load_audit()
    for item in audit["word_samples"]:
        assert item["invalid_media_count"] == 0


if __name__ == "__main__":
    test_outputs_exist()
    test_selected_sample_counts()
    test_excel_attachment_bindings_are_consistent()
    test_xixian_3_skipped_blank_row_is_visible()
    test_word_media_directory_entries_are_filtered()
    print("step 06 audit tests passed")
