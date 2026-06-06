from __future__ import annotations

import json
from pathlib import Path

from evaluate_base_cloud_qdrant import DEFAULT_EVAL_ROWS, DEFAULT_OUT_DIR


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_qdrant_eval_outputs_exist() -> None:
    assert (DEFAULT_OUT_DIR / "masked_eval_inputs.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "eval_results.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "summary.json").exists()
    assert (DEFAULT_OUT_DIR / "eval_report.md").exists()


def test_qdrant_eval_summary() -> None:
    summary = json.loads((DEFAULT_OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["retriever"] == "qdrant_full_store"
    assert summary["rows"] == DEFAULT_EVAL_ROWS
    assert summary["namespace_filter"] == ["xixian_4", "global"]
    assert "不进入 masked_query" in summary["answer_leakage_control"]


def test_heldout_not_in_masked_query() -> None:
    for result in read_jsonl(DEFAULT_OUT_DIR / "eval_results.jsonl"):
        assert result["heldout_answer"]
        assert result["heldout_answer"] not in result["masked_query"]


if __name__ == "__main__":
    test_qdrant_eval_outputs_exist()
    test_qdrant_eval_summary()
    test_heldout_not_in_masked_query()
    print("step 15 qdrant base cloud eval tests passed")
