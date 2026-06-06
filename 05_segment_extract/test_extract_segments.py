from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/05_segment_extract")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outputs_exist() -> None:
    assert (OUT_DIR / "segments.jsonl").exists()
    assert (OUT_DIR / "sheet_mappings.jsonl").exists()
    assert (OUT_DIR / "summary.json").exists()
    assert (OUT_DIR / "visualization.md").exists()


def test_expected_segment_counts() -> None:
    summary = json.loads((OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["total_segments"] == 1612
    assert summary["mapping_status_counts"]["ok"] == 10
    assert summary["segment_counts_by_data_center"]["xixian_3"] == 115
    assert summary["segment_counts_by_data_center"]["xixian_6"] == 183
    assert summary["segment_counts_by_data_center"]["global"] == 9


def test_segments_have_required_fields() -> None:
    segments = read_jsonl(OUT_DIR / "segments.jsonl")
    required = {
        "segment_id",
        "segment_type",
        "data_center_id",
        "file_id",
        "sheet_name",
        "row_index",
        "capability_desc",
        "answer_value",
        "raw_text",
        "embedding_text",
        "source_anchor",
        "proof_attachments",
    }
    for segment in segments[:50]:
        assert required.issubset(segment)
        assert segment["segment_type"] == "excel_capability_row"
        assert segment["capability_desc"]
        assert segment["answer_value"]
        assert segment["embedding_text"]


def test_attachments_are_bound_to_rows() -> None:
    segments = read_jsonl(OUT_DIR / "segments.jsonl")
    with_attachments = [segment for segment in segments if segment["proof_attachment_count"] > 0]
    assert len(with_attachments) > 900
    assert sum(segment["proof_attachment_count"] for segment in segments) == 1220
    first = with_attachments[0]
    for attachment in first["proof_attachments"]:
        assert attachment["source_cell"] in first["source_anchor"]["proof_cells"]
        assert attachment["ocr_status"] == "not_required"


def test_embedded_package_is_bound_to_row() -> None:
    segments = read_jsonl(OUT_DIR / "segments.jsonl")
    row_93 = next(
        segment
        for segment in segments
        if segment["file_name"] == "西咸数据中心6号楼维护能力知识库.xlsx" and segment["row_index"] == 93
    )
    assert row_93["proof_attachment_count"] == 1
    attachment = row_93["proof_attachments"][0]
    assert attachment["attachment_type"] == "embedded_object"
    assert attachment["anchor_type"] == "ole_object"
    assert attachment["source_cell"] == "E93"


if __name__ == "__main__":
    test_outputs_exist()
    test_expected_segment_counts()
    test_segments_have_required_fields()
    test_attachments_are_bound_to_rows()
    test_embedded_package_is_bound_to_row()
    print("step 05 tests passed")
