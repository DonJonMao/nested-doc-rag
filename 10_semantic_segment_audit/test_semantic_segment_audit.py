from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/10_semantic_segment_audit"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_audit_outputs_exist() -> None:
    assert (OUT_DIR / "semantic_audit.jsonl").exists()
    assert (OUT_DIR / "semantic_audit.md").exists()
    assert (OUT_DIR / "semantic_audit_summary.json").exists()


def test_semantic_status_counts() -> None:
    summary = read_json(OUT_DIR / "semantic_audit_summary.json")
    assert summary["total_segments"] == 1223
    assert summary["semantic_status_counts"]["low_value_incomplete_source"] == 165
    assert summary["semantic_status_counts"]["ok_aggregate"] == 45
    assert summary["semantic_status_counts"]["template_not_fact"] == 12
    assert summary["semantic_status_counts"]["schema_only"] == 5
    assert summary["semantic_flag_counts"]["needs_image_evidence"] == 20


def test_every_segment_is_audited_once() -> None:
    records = read_jsonl(OUT_DIR / "semantic_audit.jsonl")
    assert len(records) == 1223
    assert len({record["segment_id"] for record in records}) == len(records)
    assert Counter(record["embedding_policy"] for record in records)["exclude"] == 165
    assert not any(record["semantic_status"] == "needs_subject_context" for record in records)
    assert not any(record["semantic_status"] == "needs_merged_cell_inheritance" for record in records)


def test_specific_semantic_findings() -> None:
    records = read_jsonl(OUT_DIR / "semantic_audit.jsonl")
    network = next(record for record in records if record["table_category"] == "应急预案-网络设备清单")
    assert network["semantic_status"] == "low_value_incomplete_source"
    assert network["embedding_policy"] == "exclude"

    questionnaire = next(record for record in records if record["table_category"] == "服务报告-满意度评价")
    assert questionnaire["semantic_status"] == "template_not_fact"

    position = next(record for record in records if "岗位名称：机房网络工程师" in record["raw_text"] and "工作内容" in record["raw_text"])
    assert position["semantic_status"] == "ok_subject_scoped"

    image = next(record for record in records if "右图" in record["raw_text"])
    assert "needs_image_evidence" in image["semantic_flags"]


if __name__ == "__main__":
    test_audit_outputs_exist()
    test_semantic_status_counts()
    test_every_segment_is_audited_once()
    test_specific_semantic_findings()
    print("step 10 semantic segment audit tests passed")
