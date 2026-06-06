from __future__ import annotations

import json
from pathlib import Path

from build_gongkan_rag_inputs import DEFAULT_OUT_DIR, run


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outputs_exist() -> None:
    assert (DEFAULT_OUT_DIR / "agent_rag_input_templates.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "rag_question_inputs.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "summary.json").exists()
    assert (DEFAULT_OUT_DIR / "visualization.md").exists()


def test_summary_counts() -> None:
    summary = json.loads((DEFAULT_OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["target_namespace"] == "xixian_6"
    assert summary["template_count"] == 16
    assert summary["rag_request_count"] == 563
    assert summary["requests_needing_evidence"] == 55
    assert summary["counts_by_mode"]["cell_answer"] == 346
    assert summary["counts_by_mode"]["status_with_current_info"] == 196


def test_requests_have_rag_input_contract() -> None:
    requests = read_jsonl(DEFAULT_OUT_DIR / "rag_question_inputs.jsonl")
    for request in requests[:100]:
        assert request["rag_request_id"].startswith("gkrag_")
        assert request["namespace_filter"] == ["xixian_6", "global"]
        assert request["retrieval"]["query_text"]
        assert request["answer_contract"]["contract_name"] == "gongkan_rag_answer_v1"
        assert "source_chunks" in request["answer_contract"]["output_schema"]
        assert "不能使用常识或猜测" in request["answer_prompt_template"]


def test_answer_sample_is_not_fact_source() -> None:
    requests = read_jsonl(DEFAULT_OUT_DIR / "rag_question_inputs.jsonl")
    target = next(
        request
        for request in requests
        if request["form_item"]["file_name"] == "CDN机房工勘调研.xlsx"
        and request["form_item"]["target_cell"] == "C6"
    )
    assert "答复示例仅作格式参考，不可直接复制" in target["retrieval"]["query_text"]
    assert "format_hint_only" in target["form_item"]["answer_sample_policy"]


def test_assessment_matrix_uses_status_contract() -> None:
    requests = read_jsonl(DEFAULT_OUT_DIR / "rag_question_inputs.jsonl")
    target = next(
        request
        for request in requests
        if request["form_item"]["file_name"] == "陕西西安移动三线CDN机房排查表（2025.07）.xlsx"
        and request["form_item"]["sheet_name"] == "配电系统"
        and request["form_item"]["row_index"] == 7
    )
    assert target["mode"] == "status_with_current_info"
    assert "status" in target["answer_contract"]["output_schema"]
    assert "current_info" in target["answer_contract"]["output_schema"]
    assert "判断评估项满足情况" in target["retrieval"]["query_text"]


def test_agent_templates_are_structure_only() -> None:
    templates = read_jsonl(DEFAULT_OUT_DIR / "agent_rag_input_templates.jsonl")
    assert len(templates) == 16
    for template_record in templates:
        template = template_record["rag_input_template"]
        assert "rag_return_mode" in template
        assert "answer_value" not in template
        assert "include_fields" in template


if __name__ == "__main__":
    if not (DEFAULT_OUT_DIR / "summary.json").exists():
        run(use_llm=False)
    test_outputs_exist()
    test_summary_counts()
    test_requests_have_rag_input_contract()
    test_answer_sample_is_not_fact_source()
    test_assessment_matrix_uses_status_contract()
    test_agent_templates_are_structure_only()
    print("step 13 gongkan rag input tests passed")
