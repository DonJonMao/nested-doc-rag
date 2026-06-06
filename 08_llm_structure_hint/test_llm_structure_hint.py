from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/08_llm_structure_hint")
APPLIED_SEGMENTS = OUT_DIR / "hinted_table_segments.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outputs_exist() -> None:
    assert (OUT_DIR / "table_structure_hints.jsonl").exists()
    assert (OUT_DIR / "summary.json").exists()
    assert (OUT_DIR / "visualization.md").exists()


def test_hints_are_valid_json() -> None:
    records = read_jsonl(OUT_DIR / "table_structure_hints.jsonl")
    assert records
    assert all(record["validation"]["status"] == "valid" for record in records)
    assert all(isinstance(record["llm_hint"], dict) for record in records)


def test_model_only_outputs_structure() -> None:
    records = read_jsonl(OUT_DIR / "table_structure_hints.jsonl")
    for record in records:
        hint = record["llm_hint"]
        assert "column_headers" in hint
        assert "data_rows" in hint
        assert "row_strategy" in hint
        assert "raw_text" not in hint
        assert "embedding_text" not in hint


def test_summary_uses_curl_backend() -> None:
    summary = json.loads((OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["curl_backend"] is True
    assert summary["requested_tables"] >= 1


def test_applied_segments_exist_and_use_original_content() -> None:
    assert APPLIED_SEGMENTS.exists()
    segments = read_jsonl(APPLIED_SEGMENTS)
    assert segments
    assert all(
        item["source_policy"] == "llm_structure_hint_only; content_from_original_rows"
        for item in segments
    )
    for item in segments:
        for value in item["row_values"]:
            if value:
                assert value in item["raw_text"]


def test_questionnaire_headers_are_from_structure_hint() -> None:
    segments = read_jsonl(APPLIED_SEGMENTS)
    target = next(item for item in segments if item["table_no"] == 11 and item["row_index"] == 2)
    assert "评价项：服务态度、礼貌用语" in target["raw_text"]
    assert "非常满意：□ 非常满意" in target["raw_text"]
    assert "列2：" not in target["raw_text"]
    assert "列3：" not in target["raw_text"]


def test_questionnaire_group_row_postprocess() -> None:
    segments = read_jsonl(APPLIED_SEGMENTS)
    assert not any(item["table_no"] == 11 and item["row_index"] == 11 for item in segments)
    target = next(item for item in segments if item["table_no"] == 11 and item["row_index"] == 12)
    assert target["group"] == "业务使用方面"
    assert 11 in target["postprocess"]["matrix_group_rows_from_values"]


if __name__ == "__main__":
    test_outputs_exist()
    test_hints_are_valid_json()
    test_model_only_outputs_structure()
    test_summary_uses_curl_backend()
    test_applied_segments_exist_and_use_original_content()
    test_questionnaire_headers_are_from_structure_hint()
    test_questionnaire_group_row_postprocess()
    print("step 08 LLM structure hint tests passed")
