from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/09_table_candidate_resolution")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_summary_full_coverage() -> None:
    summary = read_json(OUT_DIR / "summary.json")
    assert summary["candidate_tables"] == 146
    assert summary["processed_tables"] == 146
    assert summary["processed_rate"] == 1.0
    assert summary["resolved_segments"] == 1223
    assert summary["placeholder_segment_count"] == 0
    assert summary["fallback_header_segment_count"] == 0
    assert summary["source_value_preservation_failures"] == 0
    assert summary["needs_review_tables"] == 0


def test_all_tables_resolved_or_schema_only() -> None:
    tables = read_jsonl(OUT_DIR / "resolved_tables.jsonl")
    status_counts = Counter(item["status"] for item in tables)
    assert status_counts == {"resolved": 141, "schema_only": 5}
    assert Counter(item["source_group"] for item in tables) == {
        "new_from_rar_emergency": 120,
        "original_26": 26,
    }
    assert len({item["table_id"] for item in tables}) == len(tables)


def test_segments_have_no_placeholder_columns() -> None:
    segments = read_jsonl(OUT_DIR / "resolved_table_segments.jsonl")
    assert segments
    for segment in segments:
        assert not re.search(r"列\d+：", segment["raw_text"])
        assert not segment["quality_flags"]["fallback_headers"]
        assert segment["source_policy"] == "content_from_original_rows; structure_from_rule_or_llm_hint"


def test_network_device_header_rows_are_not_data() -> None:
    segments = read_jsonl(OUT_DIR / "resolved_table_segments.jsonl")
    target = [
        item
        for item in segments
        if item["table_category"] == "应急预案-网络设备清单"
        and item["anchor"] == "咸阳数据中心!E90 table 3"
    ]
    assert target
    assert min(item["source_row_indices"][0] for item in target) == 3
    assert "序号：1；机房：咸阳" in target[0]["raw_text"]


def test_questionnaire_grouping_is_stable() -> None:
    segments = read_jsonl(OUT_DIR / "resolved_table_segments.jsonl")
    assert not any(
        item["table_category"] == "服务报告-满意度评价" and item["source_row_indices"] == [11]
        for item in segments
    )
    target = next(
        item
        for item in segments
        if item["table_category"] == "服务报告-满意度评价" and item["source_row_indices"] == [12]
    )
    assert target["segment_role"] == "questionnaire_option"
    assert target["group"] == "业务使用方面"
    assert target["raw_text"].startswith("满意度问卷模板项")
    assert "非常满意：□ 非常满意" in target["raw_text"]


def test_schema_only_tables_are_header_only_resources() -> None:
    tables = read_jsonl(OUT_DIR / "resolved_tables.jsonl")
    schema_tables = [item for item in tables if item["status"] == "schema_only"]
    assert len(schema_tables) == 5
    assert all(item["table_category"] == "应急预案-应急资源备件" for item in schema_tables)


def test_subject_and_merged_cell_context_are_preserved() -> None:
    segments = read_jsonl(OUT_DIR / "resolved_table_segments.jsonl")
    position = next(item for item in segments if "岗位名称：机房网络工程师" in item["raw_text"] and "工作内容" in item["raw_text"])
    assert position["table_subject"] == "岗位名称：机房网络工程师；所属部门：技术组"

    cabinet = next(item for item in segments if item["table_category"] == "服务报告-资源机柜分布" and "三楼北机房" in item["raw_text"])
    assert cabinet["raw_text"].startswith("数据中心名称：咸阳数据中心；机房：三楼北机房")

    note = next(item for item in segments if item["table_category"] == "绩效考核-年度考核明细" and item["segment_role"] == "note")
    assert note["raw_text"] == "备注：出现重大事件，主要责任人员当月进行降岗，且年底升岗资格冻结。"


def test_procedure_aggregate_segments_exist() -> None:
    segments = read_jsonl(OUT_DIR / "resolved_table_segments.jsonl")
    role_counts = Counter(item["segment_role"] for item in segments)
    assert role_counts["procedure"] == 5
    assert role_counts["table_procedure"] == 40
    assert any(item["raw_text"].startswith("零信任登录4A完整步骤") for item in segments)
    assert any(item["raw_text"].startswith("完整命令序列") for item in segments)
    assert any(item["raw_text"].startswith("完整排查步骤") for item in segments)


if __name__ == "__main__":
    test_summary_full_coverage()
    test_all_tables_resolved_or_schema_only()
    test_segments_have_no_placeholder_columns()
    test_network_device_header_rows_are_not_data()
    test_questionnaire_grouping_is_stable()
    test_schema_only_tables_are_header_only_resources()
    test_subject_and_merged_cell_context_are_preserved()
    test_procedure_aggregate_segments_exist()
    print("step 09 table candidate resolution tests passed")
