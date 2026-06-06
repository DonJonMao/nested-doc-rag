from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/04b_embedded_object_parse")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outputs_exist() -> None:
    assert (OUT_DIR / "embedded_objects.jsonl").exists()
    assert (OUT_DIR / "embedded_segments.jsonl").exists()
    assert (OUT_DIR / "summary.json").exists()
    assert (OUT_DIR / "visualization.md").exists()


def test_xixian_6_embedded_objects_are_detected() -> None:
    summary = json.loads((OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["total_embedded_objects"] >= 11
    assert summary["object_counts_by_type"]["rar"] >= 4
    assert summary["object_counts_by_type"]["pdf"] >= 4
    assert summary["object_counts_by_type"]["docx"] >= 1


def test_row_93_rar_has_parent_tags() -> None:
    objects = read_jsonl(OUT_DIR / "embedded_objects.jsonl")
    row_93 = next(item for item in objects if item["parent_file_id"] == "file_d203075f61" and item["parent_source_cell"] == "E93")
    assert row_93["parent_segment_id"] == "seg_file_d203075f61_01_row_0093"
    assert row_93["embedded_file_name"] == "NOC监控-7_24.rar"
    assert row_93["embedded_file_type"] == "rar"
    assert row_93["parse_status"] == "parsed_archive"
    assert row_93["child_file_count"] == 5
    assert Path(row_93["embedded_payload_path"]).exists()


def test_embedded_word_generates_child_segments() -> None:
    segments = read_jsonl(OUT_DIR / "embedded_segments.jsonl")
    row_94_segments = [
        segment
        for segment in segments
        if segment["parent_file_id"] == "file_d203075f61" and segment["parent_source_cell"] == "E94"
    ]
    assert row_94_segments
    assert all(segment["parent_segment_id"] == "seg_file_d203075f61_01_row_0094" for segment in row_94_segments)
    assert any(segment["segment_type"] == "embedded_docx_paragraph" for segment in row_94_segments)


def test_rar_docx_children_are_parsed() -> None:
    segments = read_jsonl(OUT_DIR / "embedded_segments.jsonl")
    row_95_segments = [
        segment
        for segment in segments
        if segment["parent_file_id"] == "file_d203075f61" and segment["parent_source_cell"] == "E95"
    ]
    assert row_95_segments
    assert any("应急预案" in (segment.get("embedded_file_name") or "") for segment in row_95_segments)


def test_legacy_word_documents_are_text_converted() -> None:
    segments = read_jsonl(OUT_DIR / "embedded_segments.jsonl")
    legacy_segments = [
        segment
        for segment in segments
        if segment["parent_file_id"] == "file_b9de462c2b" and segment["parent_source_cell"] == "E104"
    ]
    assert legacy_segments
    assert any("城东数据中心" in segment["raw_text"] for segment in legacy_segments)


def test_embedded_word_table_rows_keep_heading_and_headers() -> None:
    segments = read_jsonl(OUT_DIR / "embedded_segments.jsonl")
    network_resource_row = next(
        segment
        for segment in segments
        if segment["parent_segment_id"] == "seg_file_d203075f61_01_row_0094"
        and segment["local_anchor"].get("table_index") == 1
        and segment["local_anchor"].get("row_index") == 2
    )
    assert network_resource_row["local_anchor"]["section_context"] == ["陕西移动西咸IDC数据中心网络资源"]
    assert network_resource_row["local_anchor"]["table_header"] == ["业务链路", "接入设备", "接入端口", "实际带宽", "ODF信息"]
    assert "业务链路：2*1.5G" in network_resource_row["raw_text"]
    assert "接入设备：西咸4号楼CE16816-6" in network_resource_row["raw_text"]

    patrol_row = next(
        segment
        for segment in segments
        if segment["parent_segment_id"] == "seg_file_d203075f61_01_row_0094"
        and segment["local_anchor"].get("table_index") == 2
        and segment["local_anchor"].get("row_index") == 3
    )
    assert patrol_row["local_anchor"]["table_header"] == ["类别", "项目", "结果", "异常描述"]
    assert "类别：接入设备" in patrol_row["raw_text"]
    assert "项目：告警情况" in patrol_row["raw_text"]


if __name__ == "__main__":
    test_outputs_exist()
    test_xixian_6_embedded_objects_are_detected()
    test_row_93_rar_has_parent_tags()
    test_embedded_word_generates_child_segments()
    test_rar_docx_children_are_parsed()
    test_legacy_word_documents_are_text_converted()
    test_embedded_word_table_rows_keep_heading_and_headers()
    print("step 04B embedded object tests passed")
