from __future__ import annotations

import json
from pathlib import Path

from evaluate_base_cloud_form import (
    DEFAULT_EVAL_ROWS,
    DEFAULT_OUT_DIR,
    DEFAULT_TARGET_NAMESPACE,
    build_answer_messages,
    build_masked_query,
    select_eval_items,
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_eval_outputs_exist() -> None:
    assert (DEFAULT_OUT_DIR / "masked_eval_inputs.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "eval_results.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "summary.json").exists()
    assert (DEFAULT_OUT_DIR / "eval_report.md").exists()


def test_summary_uses_xixian_4_closed_book_sample() -> None:
    summary = json.loads((DEFAULT_OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["target_namespace"] == DEFAULT_TARGET_NAMESPACE
    assert summary["rows"] == DEFAULT_EVAL_ROWS
    assert summary["sample_count"] == 10
    assert summary["index_namespaces"] == ["global", "xixian_4"]
    assert "不进入 masked_query" in summary["answer_leakage_control"]


def test_heldout_answer_not_in_masked_query() -> None:
    items_by_row = {item["row_index"]: item for item in select_eval_items(DEFAULT_EVAL_ROWS)}
    masked_inputs = read_jsonl(DEFAULT_OUT_DIR / "masked_eval_inputs.jsonl")
    assert len(masked_inputs) == 10
    for masked in masked_inputs:
        heldout_answer = str(items_by_row[masked["row_index"]].get("existing_value") or "").strip()
        assert heldout_answer
        assert heldout_answer not in masked["query_text"]


def test_heldout_answer_not_in_answer_prompt() -> None:
    dummy_hits = [
        {
            "chunk_id": "dummy_chunk",
            "rerank_rank": 1,
            "rerank_score": 1.0,
            "namespace": DEFAULT_TARGET_NAMESPACE,
            "anchor": "dummy!row 1",
            "raw_text": "仅用于测试的检索证据，不包含答案。",
            "proof_attachment_ids": [],
        }
    ]
    for item in select_eval_items(DEFAULT_EVAL_ROWS):
        heldout_answer = str(item.get("existing_value") or "").strip()
        query_text = build_masked_query(item, DEFAULT_TARGET_NAMESPACE)
        messages = build_answer_messages(item, query_text, dummy_hits)
        prompt_text = "\n".join(message["content"] for message in messages)
        assert heldout_answer not in prompt_text


def test_eval_result_keeps_heldout_only_for_judge() -> None:
    results = read_jsonl(DEFAULT_OUT_DIR / "eval_results.jsonl")
    assert len(results) == 10
    for result in results:
        assert result["heldout_answer"]
        assert result["heldout_answer"] not in result["masked_query"]
        assert "generated_answer" in result
        assert "judge" in result
        assert "top_hits" in result


if __name__ == "__main__":
    test_eval_outputs_exist()
    test_summary_uses_xixian_4_closed_book_sample()
    test_heldout_answer_not_in_masked_query()
    test_heldout_answer_not_in_answer_prompt()
    test_eval_result_keeps_heldout_only_for_judge()
    print("step 14 base cloud closed-book eval tests passed")
