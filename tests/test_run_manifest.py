from __future__ import annotations

from pathlib import Path
from typing import Any

from nested_doc_rag.agent.step15_runner import Step15AgentRunner
from nested_doc_rag.config import load_app_config
from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult
from nested_doc_rag.io import read_json


def test_manifest_generated_after_fake_run(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, answer_caller=answered_answer_caller)

    runner.run([make_item(4)])

    manifest = read_json(tmp_path / "run_manifest.json")
    assert manifest["engine"] == "step15_agent_overlay"
    assert manifest["status"] == "completed"
    assert manifest["artifacts"]["predictions_raw"] == "predictions_raw.jsonl"
    assert manifest["artifacts"]["agent_overlays"] == "agent_overlays.jsonl"
    assert manifest["counts"]["total_fields"] == 1
    assert manifest["counts"]["answered"] == 1
    assert manifest["counts"]["writeback_allowed"] == 1


def test_manifest_contains_artifact_paths(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, answer_caller=answered_answer_caller)

    runner.run([make_item(4)])

    manifest = read_json(tmp_path / "run_manifest.json")
    for key in [
        "predictions_raw",
        "predictions",
        "agent_overlays",
        "predictions_agent_view",
        "review_items",
        "trace",
        "trace_summary",
        "run_summary",
        "summary",
    ]:
        assert manifest["artifacts"][key]
        assert (tmp_path / manifest["artifacts"][key]).exists()


def test_failed_run_still_writes_manifest(tmp_path: Path) -> None:
    def failing_answer_caller(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("fake generation failure")

    runner = make_runner(tmp_path, answer_caller=failing_answer_caller)

    predictions = runner.run([make_item(4)])

    manifest = read_json(tmp_path / "run_manifest.json")
    assert predictions[0].answer_status == "conflict_unresolved"
    assert manifest["status"] == "failed"
    assert manifest["counts"]["failed"] == 1
    assert manifest["counts"]["conflict_unresolved"] == 1


def make_runner(tmp_path: Path, *, answer_caller) -> Step15AgentRunner:
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")
    return Step15AgentRunner(
        config=config,
        target_namespace="xixian_4",
        global_namespace="global",
        room_context="西咸4号楼 301机房",
        out_dir=tmp_path,
        retrieval_mode="layered",
        retrieval_fn=fake_retrieval,
        answer_caller=answer_caller,
        chat_retry_backoff_seconds=0,
    )


def make_item(row: int) -> dict[str, Any]:
    return {
        "form_item_id": f"item_{row}",
        "file_name": "基地云机房信息调研表.xlsx",
        "sheet_name": "Sheet1",
        "row_index": row,
        "target_cell": f"D{row}",
        "category_path": ["电力", "市电"],
        "question_text": "市电进线情况",
        "instruction_text": "填写市电路数及来源",
        "answer_example": "2路市电",
        "existing_value": "2路市电",
    }


def fake_retrieval(query: str) -> Step15RetrievalResult:
    del query
    hits = [
        {
            "chunk_id": "chunk_main",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "corpus_layer": "fact",
            "retrieval_layer": "target_main_fact",
            "raw_text": "市电进线情况：2路市电。",
            "text_for_embedding": "市电进线情况 2路市电",
        }
    ]
    return Step15RetrievalResult(reranked_hits=hits, vector_hits=hits, retrieval_mode="layered")


def answered_answer_caller(**kwargs: Any) -> dict[str, Any]:
    return {
        "answer_value": "2路市电",
        "answer_status": "answered",
        "confidence": 0.9,
        "source_chunk_ids": [kwargs["hits"][0]["chunk_id"]],
        "reference_source_documents": [],
    }
