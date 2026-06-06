from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from nested_doc_rag.agent.policies import (
    build_query_plan,
    make_prediction_from_evidence,
    retrieve_from_mini_corpus,
    select_evidence,
)
from nested_doc_rag.agent.runner import FieldFillingAgent
from nested_doc_rag.io import read_jsonl
from nested_doc_rag.schemas.eval import FieldGold


def load_mini_golds() -> list[FieldGold]:
    root = Path(__file__).resolve().parents[1]
    return [FieldGold.from_dict(record) for record in read_jsonl(root / "examples/mini_data/gold_fields.jsonl")]


def load_mini_corpus() -> list[dict]:
    root = Path(__file__).resolve().parents[1]
    return read_jsonl(root / "examples/mini_data/knowledge_chunks.jsonl")


def by_id(fields: list[FieldGold], field_id: str) -> FieldGold:
    return next(field for field in fields if field.field_id == field_id)


def test_build_query_plan() -> None:
    field = by_id(load_mini_golds(), "field_text")

    plan = build_query_plan(field, target_namespace="xixian_4", room_context="301机房")

    assert "机房名称" in plan.primary_query
    assert "xixian_4" in plan.primary_query
    assert plan.intent == "room_identity"
    assert plan.required_evidence is True


def test_target_namespace_beats_global_intro() -> None:
    field = by_id(load_mini_golds(), "field_text")
    corpus = load_mini_corpus()
    plan = build_query_plan(field, target_namespace="xixian_4")

    candidates = retrieve_from_mini_corpus(plan, corpus, field)
    bundle = select_evidence(candidates, field, plan)
    prediction = make_prediction_from_evidence(field, bundle)

    assert bundle.selected_chunks[0]["chunk_id"] == "chunk_room_name"
    assert any(chunk["chunk_id"] == "chunk_global_room_name" for chunk in [*bundle.ignored_chunks, *bundle.reference_chunks])
    assert prediction.answer_status == "answered"
    assert prediction.source_chunk_ids == ["chunk_room_name"]


def test_global_only_becomes_partial_clue() -> None:
    field = by_id(load_mini_golds(), "field_text")
    corpus = [chunk for chunk in load_mini_corpus() if chunk["chunk_id"] == "chunk_global_room_name"]
    plan = build_query_plan(field, target_namespace="xixian_4")

    bundle = select_evidence(retrieve_from_mini_corpus(plan, corpus, field), field, plan)
    prediction = make_prediction_from_evidence(field, bundle)

    assert prediction.answer_status == "partial_clue"
    assert prediction.source_chunk_ids == []
    assert prediction.validation["reference_chunk_ids"] == ["chunk_global_room_name"]


def test_no_evidence_becomes_not_found(tmp_path: Path) -> None:
    field = by_id(load_mini_golds(), "field_text")
    agent = FieldFillingAgent(target_namespace="xixian_4", corpus=[], out_dir=tmp_path, max_repair_attempts=1)

    predictions = agent.run([field])

    assert predictions[0].answer_status == "not_found"
    assert predictions[0].source_chunk_ids == []
    assert read_jsonl(tmp_path / "review_items.jsonl")[0]["reason"] == "not_found"


def test_conflict_goes_to_human_review(tmp_path: Path) -> None:
    field = by_id(load_mini_golds(), "field_enum")
    corpus = [
        {
            "chunk_id": "chunk_power_a",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "field_id": "field_enum",
            "question_text": "是否满足双路供电",
            "text": "是否满足双路供电：满足。",
            "answer_value": "满足",
            "answer_status": "answered",
            "source_chunk_ids": ["chunk_power_a"],
        },
        {
            "chunk_id": "chunk_power_b",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "field_id": "field_enum",
            "question_text": "是否满足双路供电",
            "text": "是否满足双路供电：不满足。",
            "answer_value": "不满足",
            "answer_status": "answered",
            "source_chunk_ids": ["chunk_power_b"],
        },
    ]
    agent = FieldFillingAgent(target_namespace="xixian_4", corpus=corpus, out_dir=tmp_path)

    predictions = agent.run([field])

    assert predictions[0].answer_status == "conflict_unresolved"
    assert read_jsonl(tmp_path / "review_items.jsonl")[0]["reason"] == "conflict_unresolved"


def test_bool_uncertain_can_use_embedded_word_table() -> None:
    field = by_id(load_mini_golds(), "field_bool")
    corpus = load_mini_corpus()
    plan = build_query_plan(field, target_namespace="xixian_4")

    bundle = select_evidence(retrieve_from_mini_corpus(plan, corpus, field), field, plan)
    prediction = make_prediction_from_evidence(field, bundle)

    assert bundle.selected_chunks[0]["chunk_id"] == "chunk_access"
    assert prediction.answer_value == "是"
    assert prediction.source_chunk_ids == ["chunk_access"]


def test_repair_once(tmp_path: Path) -> None:
    field = FieldGold.from_dict(
        {
            "field_id": "field_repair_date",
            "row_index": 9,
            "target_cell": "C9",
            "question_text": "最近巡检日期",
            "expected_value": "2025-07-01",
            "field_type": "date",
            "required": True,
            "must_have_evidence": True,
        }
    )
    corpus = [
        {
            "chunk_id": "chunk_repair_date",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "field_id": "field_repair_date",
            "question_text": "最近巡检日期",
            "text": "最近巡检日期：2025年7月1号。",
            "answer_value": "2025年7月1号",
            "answer_status": "answered",
            "source_chunk_ids": ["chunk_repair_date"],
        }
    ]
    agent = FieldFillingAgent(target_namespace="xixian_4", corpus=corpus, out_dir=tmp_path, max_repair_attempts=1)

    predictions = agent.run([field])

    assert predictions[0].answer_value == "2025-07-01"
    assert len(agent.field_states[0].repair_attempts) == 1
    assert any(event["step"] == "repaired" for event in read_jsonl(tmp_path / "trace.jsonl"))


def test_run_agent_outputs(tmp_path: Path) -> None:
    agent = FieldFillingAgent(target_namespace="xixian_4", corpus=load_mini_corpus(), out_dir=tmp_path)

    predictions = agent.run(load_mini_golds())

    assert len(predictions) == 5
    for file_name in ["predictions.jsonl", "trace.jsonl", "trace_summary.json", "trace.md", "review_items.jsonl", "run_summary.md"]:
        assert (tmp_path / file_name).exists()


def test_cli_run_agent(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "nested_doc_rag.cli",
            "run-agent",
            "--config",
            "config/local.example.yaml",
            "--gold",
            "examples/mini_data/gold_fields.jsonl",
            "--corpus",
            "examples/mini_data/knowledge_chunks.jsonl",
            "--target-namespace",
            "xixian_4",
            "--out-dir",
            str(tmp_path),
            "--no-writeback",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["field_count"] == 5
    assert (tmp_path / "predictions.jsonl").exists()
    assert (tmp_path / "trace_summary.json").exists()
