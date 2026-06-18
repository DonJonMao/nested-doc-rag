from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from nested_doc_rag.artifacts import validate_step15_artifacts
from nested_doc_rag.agent.step15_runner import Step15AgentRunner
from nested_doc_rag.config import AgentScopeConfig, load_app_config
from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult
from nested_doc_rag.io import read_json, read_jsonl, write_jsonl
from nested_doc_rag.schemas.eval import FieldPrediction


def test_off_mode_writes_original_core_artifacts_without_mas_trace(tmp_path: Path) -> None:
    out_dir = tmp_path / "off"
    runner = make_runner(out_dir, mode="off")

    predictions = runner.run([make_item(4)])

    assert predictions[0].answer_status == "answered"
    assert not (out_dir / "mas_trace.jsonl").exists()
    assert not (out_dir / "agentscope_events.jsonl").exists()
    assert read_jsonl(out_dir / "predictions_raw.jsonl") == read_jsonl(out_dir / "predictions.jsonl")
    assert validate_step15_artifacts(out_dir)["valid"] is True


def test_equivalent_mas_core_artifacts_match_off_mode(tmp_path: Path) -> None:
    off_dir = tmp_path / "off"
    mas_dir = tmp_path / "mas"

    make_runner(off_dir, mode="off").run([make_item(4), make_item(5, question_text="液冷是否支持")])
    make_runner(mas_dir, mode="equivalent_mas").run([make_item(4), make_item(5, question_text="液冷是否支持")])

    assert_core_artifacts_equal(off_dir, mas_dir)
    assert (mas_dir / "mas_trace.jsonl").exists()
    assert (mas_dir / "agentscope_events.jsonl").exists()
    events = read_jsonl(mas_dir / "agentscope_events.jsonl")
    assert events[0]["agentscope_available"] is True
    assert str(events[0]["agentscope_version"]).startswith("2.")
    assert [event["role"] for event in events if event.get("event_type") == "role_invoked"] == [
        "query_planner",
        "evidence_retrieval",
        "answer_arbitration",
        "overlay_control",
        "query_planner",
        "evidence_retrieval",
        "answer_arbitration",
        "overlay_control",
    ]
    assert validate_step15_artifacts(mas_dir)["valid"] is True


def test_default_config_runs_agentscope_equivalent_mas(tmp_path: Path) -> None:
    off_dir = tmp_path / "off"
    default_dir = tmp_path / "default"

    make_runner(off_dir, mode="off").run([make_item(4)])
    make_default_runner(default_dir).run([make_item(4)])

    assert_core_artifacts_equal(off_dir, default_dir)
    events = read_jsonl(default_dir / "agentscope_events.jsonl")
    assert events[0]["mode"] == "equivalent_mas"
    assert events[0]["agentscope_available"] is True
    assert any(event.get("role") == "query_planner" for event in events)


def test_trace_only_core_artifacts_match_off_mode_except_optional_trace(tmp_path: Path) -> None:
    off_dir = tmp_path / "off"
    trace_dir = tmp_path / "trace_only"

    make_runner(off_dir, mode="off").run([make_item(4), make_item(5, question_text="液冷是否支持")])
    make_runner(trace_dir, mode="trace_only").run([make_item(4), make_item(5, question_text="液冷是否支持")])

    assert_core_artifacts_equal(off_dir, trace_dir)
    assert read_jsonl(trace_dir / "mas_trace.jsonl")
    assert read_jsonl(trace_dir / "agentscope_events.jsonl")
    assert validate_step15_artifacts(trace_dir)["valid"] is True


def test_equivalent_mas_preserves_critic_overlay_and_source_validation(tmp_path: Path) -> None:
    mas_dir = tmp_path / "mas"
    make_runner(mas_dir, mode="equivalent_mas").run([make_item(57, question_text="液冷是否支持")])

    raw = read_jsonl(mas_dir / "predictions_raw.jsonl")[0]
    overlay = read_jsonl(mas_dir / "agent_overlays.jsonl")[0]
    review = read_jsonl(mas_dir / "review_items.jsonl")[0]

    assert raw["validation"]["source_ids_valid"] is True
    assert "liquid_cooling_scope_mismatch" in overlay["critic_flags"]
    assert overlay["review_required"] is True
    assert overlay["writeback_allowed"] is False
    assert review["writeback_allowed"] is False
    assert review["risk_level"] == overlay["risk_level"]


def test_equivalent_mas_resume_skips_completed_checkpoint(tmp_path: Path) -> None:
    completed = FieldPrediction(
        field_id="item_4",
        row_index=4,
        target_cell="D4",
        answer_value="已完成",
        answer_status="answered",
        confidence=0.9,
        method_name="step15_agent",
    )
    write_jsonl(tmp_path / "predictions.checkpoint.jsonl", [completed.to_dict()])
    calls: list[str] = []

    runner = make_runner(tmp_path, mode="equivalent_mas", retrieval_fn=recording_retrieval(calls), resume=True)
    predictions = runner.run([make_item(4), make_item(5)])

    assert [prediction.row_index for prediction in predictions] == [4, 5]
    assert len(calls) == 1
    assert validate_step15_artifacts(tmp_path)["valid"] is True


def assert_core_artifacts_equal(left: Path, right: Path) -> None:
    for file_name in ["predictions_raw.jsonl", "predictions.jsonl", "agent_overlays.jsonl", "review_items.jsonl"]:
        assert read_jsonl(left / file_name) == read_jsonl(right / file_name)

    left_summary = read_json(left / "summary.json")
    right_summary = read_json(right / "summary.json")
    for key in [
        "answer_status_counts",
        "raw_status_counts",
        "overlay_counts",
        "writeback_status",
        "acceptable_or_better",
        "partial_or_better",
    ]:
        assert left_summary[key] == right_summary[key]
    for key in ["answered_count", "partial_clue_count", "not_found_count", "conflict_unresolved_count", "review_count", "failed_count"]:
        assert left_summary["trace_summary"][key] == right_summary["trace_summary"][key]

    left_manifest = read_json(left / "run_manifest.json")
    right_manifest = read_json(right / "run_manifest.json")
    assert left_manifest["counts"] == right_manifest["counts"]


def make_runner(
    out_dir: Path,
    *,
    mode: str,
    retrieval_fn: Callable[[str], Step15RetrievalResult] | None = None,
    resume: bool = False,
) -> Step15AgentRunner:
    config = load_app_config(project_root=out_dir, default_config=out_dir / "missing.yaml")
    config = replace(config, agentscope=AgentScopeConfig(enabled=mode != "off", mode=mode))
    return Step15AgentRunner(
        config=config,
        target_namespace="xixian_4",
        global_namespace="global",
        room_context="西咸4号楼 301机房",
        out_dir=out_dir,
        retrieval_plan="layered",
        retrieval_fn=retrieval_fn or fake_retrieval,
        answer_caller=answer_caller,
        writeback_enabled=False,
        resume=resume,
        chat_retry_backoff_seconds=0,
    )


def make_default_runner(out_dir: Path) -> Step15AgentRunner:
    config = load_app_config(project_root=out_dir, default_config=out_dir / "missing.yaml")
    return Step15AgentRunner(
        config=config,
        target_namespace="xixian_4",
        global_namespace="global",
        room_context="西咸4号楼 301机房",
        out_dir=out_dir,
        retrieval_plan="layered",
        retrieval_fn=fake_retrieval,
        answer_caller=answer_caller,
        writeback_enabled=False,
        chat_retry_backoff_seconds=0,
    )


def make_item(row: int, *, question_text: str = "市电进线情况") -> dict[str, Any]:
    return {
        "form_item_id": f"item_{row}",
        "file_name": "基地云机房信息调研表.xlsx",
        "sheet_name": "Sheet1",
        "row_index": row,
        "target_cell": f"D{row}",
        "category_path": ["电力", "市电"],
        "question_text": question_text,
        "instruction_text": "填写市电路数及来源",
        "answer_example": "2路市电",
        "existing_value": "2路市电",
        "needs_evidence": True,
    }


def make_hits() -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": "chunk_main",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "corpus_layer": "fact",
            "retrieval_layer": "target_main_fact",
            "layer_priority": 1,
            "rerank_score": 0.92,
            "file_name": "main.xlsx",
            "anchor": "row 12",
            "raw_text": "市电进线情况：2路市电，来自同一变电站。",
            "text_for_embedding": "市电进线情况 2路市电",
            "proof_attachment_ids": ["att_1"],
        },
        {
            "chunk_id": "chunk_global",
            "namespace": "global",
            "source_type": "intro_doc_paragraph",
            "corpus_layer": "intro_doc",
            "retrieval_layer": "global_intro",
            "layer_priority": 4,
            "rerank_score": 0.65,
            "file_name": "intro.docx",
            "anchor": "P3",
            "raw_text": "园区供电有双路市电规划。",
            "text_for_embedding": "园区供电 双路市电",
        },
    ]


def fake_retrieval(query: str) -> Step15RetrievalResult:
    del query
    hits = make_hits()
    return Step15RetrievalResult(reranked_hits=hits, vector_hits=hits, retrieval_mode="layered")


def recording_retrieval(calls: list[str]) -> Callable[[str], Step15RetrievalResult]:
    def retrieve(query: str) -> Step15RetrievalResult:
        calls.append(query)
        return fake_retrieval(query)

    return retrieve


def answer_caller(**kwargs: Any) -> dict[str, Any]:
    return {
        "answer_value": "2路市电，来自同一变电站",
        "answer_status": "answered",
        "confidence": 0.86,
        "source_chunk_ids": [kwargs["hits"][0]["chunk_id"]],
        "evidence_attachment_ids": ["att_1"],
        "reference_source_documents": [],
        "agent_resolution": {"used": True, "action": "select_source", "reason": "主表证据直接命中"},
    }
