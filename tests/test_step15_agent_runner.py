from __future__ import annotations

from pathlib import Path
from typing import Any

from nested_doc_rag.agent.step15_runner import (
    Step15AgentRunner,
    build_agent_overlay_for_step15_prediction,
    convert_step15_generated_to_prediction,
    critic_check_step15_answer,
    make_step15_review_item,
)
from nested_doc_rag.artifacts import validate_step15_artifacts
from nested_doc_rag.cli import build_parser, resolve_step15_retrieval_plan
from nested_doc_rag.config import load_app_config
from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult
from nested_doc_rag.io import read_jsonl, write_jsonl
from nested_doc_rag.schemas.eval import FieldPrediction


def test_convert_step15_generated_to_prediction_answered() -> None:
    prediction = convert_step15_generated_to_prediction(
        make_item(4),
        {
            "answer_value": "2路市电",
            "answer_status": "answered",
            "confidence": 0.86,
            "source_chunk_ids": ["chunk_main"],
            "evidence_attachment_ids": ["att_1"],
            "agent_resolution": {"used": True, "action": "select_source", "reason": "主表命中"},
        },
        make_hits(),
    )

    assert prediction.answer_status == "answered"
    assert prediction.source_chunk_ids == ["chunk_main"]
    assert prediction.evidence_attachment_ids == ["att_1"]
    assert prediction.validation["engine"] == "step15_agent"
    assert prediction.validation["source_ids_valid"] is True


def test_convert_step15_generated_to_prediction_partial() -> None:
    prediction = convert_step15_generated_to_prediction(
        make_item(5),
        {
            "answer_value": "未找到",
            "answer_status": "partial_clue",
            "confidence": 0.45,
            "source_chunk_ids": [],
            "reference_source_documents": [
                {"file_name": "intro.docx", "anchor": "P3", "chunk_id": "chunk_global", "reason": "只有园区级说明"}
            ],
        },
        make_hits(),
    )

    assert prediction.answer_status == "partial_clue"
    assert prediction.reference_chunk_ids == ["chunk_global"]
    assert prediction.reference_source_documents[0]["retrieval_layer"] == "global_intro"
    assert prediction.reference_snippets


def test_critic_answered_without_source() -> None:
    flags = critic_check_step15_answer(make_item(4), {"answer_status": "answered", "answer_value": "2路市电", "source_chunk_ids": []}, make_hits())

    assert "answered_without_source" in flags


def test_critic_partial_without_reference() -> None:
    flags = critic_check_step15_answer(
        make_item(5),
        {"answer_status": "partial_clue", "answer_value": "未找到", "source_chunk_ids": [], "reference_source_documents": []},
        make_hits(),
    )

    assert "partial_without_reference" in flags


def test_overlay_does_not_mutate_raw_prediction() -> None:
    prediction = convert_step15_generated_to_prediction(
        make_item(5),
        {"answer_value": "未找到", "answer_status": "not_found", "confidence": 0.2, "source_chunk_ids": []},
        make_hits(),
    )

    overlay = build_agent_overlay_for_step15_prediction(prediction, make_hits(), ["not_found_with_relevant_hits"])

    assert prediction.answer_status == "not_found"
    assert prediction.answer_value == "未找到"
    assert overlay.suggested_status == "partial_clue"
    assert overlay.review_required is True
    assert overlay.writeback_allowed is False
    assert overlay.suggested_reference_chunk_ids
    assert overlay.suggested_reference_source_documents
    assert overlay.suggested_reference_snippets
    assert "not_found_with_relevant_hits" in overlay.reasons


def test_partial_without_reference_gets_reference_docs() -> None:
    prediction = convert_step15_generated_to_prediction(
        make_item(5),
        {"answer_value": "未找到", "answer_status": "partial_clue", "confidence": 0.45, "reference_source_documents": []},
        make_hits(),
    )

    overlay = build_agent_overlay_for_step15_prediction(prediction, make_hits(), ["partial_without_reference"])

    assert prediction.answer_status == "partial_clue"
    assert prediction.reference_source_documents == []
    assert overlay.suggested_reference_chunk_ids
    assert overlay.suggested_reference_source_documents
    assert "reference_docs_filled_by_runner" in overlay.reasons


def test_risky_answered_overlay_blocks_writeback_without_mutating() -> None:
    prediction = convert_step15_generated_to_prediction(
        make_item(5),
        {"answer_value": "2路市电", "answer_status": "answered", "confidence": 0.82, "source_chunk_ids": []},
        make_hits(),
    )

    overlay = build_agent_overlay_for_step15_prediction(prediction, make_hits(), ["liquid_cooling_scope_mismatch"])

    assert prediction.answer_status == "answered"
    assert prediction.answer_value == "2路市电"
    assert overlay.suggested_status == "partial_clue"
    assert overlay.review_required is True
    assert overlay.writeback_allowed is False
    assert "risky_answered_requires_review" in overlay.reasons


def test_not_found_without_hits_stays_not_found() -> None:
    prediction = convert_step15_generated_to_prediction(
        make_item(5),
        {"answer_value": "未找到", "answer_status": "not_found", "confidence": 0.2, "source_chunk_ids": []},
        [],
    )

    overlay = build_agent_overlay_for_step15_prediction(prediction, [], [])

    assert prediction.answer_status == "not_found"
    assert overlay.suggested_status is None
    assert overlay.suggested_reference_chunk_ids == []


def test_review_routing_for_partial() -> None:
    prediction = convert_step15_generated_to_prediction(
        make_item(5),
        {
            "answer_value": "未找到",
            "answer_status": "partial_clue",
            "confidence": 0.45,
            "reference_source_documents": [
                {"file_name": "intro.docx", "anchor": "P3", "chunk_id": "chunk_global", "reason": "只有园区级说明"}
            ],
        },
        make_hits(),
    )

    overlay = build_agent_overlay_for_step15_prediction(prediction, make_hits(), [])
    item = make_step15_review_item(make_item(5), prediction, overlay, make_hits())

    assert item is not None
    assert item["answer_status"] == "partial_clue"
    assert item["suggested_action"] == "根据 reference_source_documents 人工确认是否可填写。"


def test_checkpoint_writes_each_field(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, answer_caller=answered_answer_caller)

    predictions = runner.run([make_item(4), make_item(5)])

    assert len(predictions) == 2
    assert len(read_jsonl(tmp_path / "predictions.checkpoint.jsonl")) == 2
    assert len(read_jsonl(tmp_path / "agent_overlays.checkpoint.jsonl")) == 2
    assert (tmp_path / "trace.checkpoint.jsonl").exists()
    assert (tmp_path / "review_items.checkpoint.jsonl").exists()
    assert (tmp_path / "run_state.json").exists()


def test_predictions_json_is_raw(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, answer_caller=not_found_answer_caller)

    predictions = runner.run([make_item(4)])

    assert predictions[0].answer_status == "not_found"
    raw = read_jsonl(tmp_path / "predictions_raw.jsonl")
    compat = read_jsonl(tmp_path / "predictions.jsonl")
    overlays = read_jsonl(tmp_path / "agent_overlays.jsonl")
    agent_view = read_jsonl(tmp_path / "predictions_agent_view.jsonl")
    assert raw == compat
    assert raw[0]["answer_status"] == "not_found"
    assert overlays[0]["suggested_status"] == "partial_clue"
    assert agent_view[0]["answer_status"] == "not_found"
    assert agent_view[0]["agent_overlay"]["suggested_status"] == "partial_clue"


def test_dense_default_artifact_contract_unchanged(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, answer_caller=answered_answer_caller)

    runner.run([make_item(4)])

    assert validate_step15_artifacts(tmp_path)["valid"] is True
    manifest = read_jsonl(tmp_path / "predictions_raw.jsonl")[0]
    assert "evidence_strength" not in manifest
    summary = load_json_file(tmp_path / "summary.json")
    assert summary["retrieval_fusion_mode"] == "dense"


def test_grounding_blocks_unsupported_answer_without_mutating_raw(tmp_path: Path) -> None:
    captured: list[int] = []
    template = tmp_path / "template.xlsx"
    template.write_text("fake", encoding="utf-8")
    runner = make_runner(
        tmp_path,
        answer_caller=unsupported_answer_caller,
        retrieval_fn=fake_retrieval_without_answer_value,
        writeback_enabled=True,
        template_path=template,
        writeback_fn=capturing_writeback(captured),
        grounding_enabled=True,
        config_overrides={"agentscope": {"enabled": False, "mode": "off"}},
    )

    predictions = runner.run([make_item(4)])

    assert predictions[0].answer_status == "answered"
    assert captured == [0]
    raw = read_jsonl(tmp_path / "predictions_raw.jsonl")[0]
    overlays = read_jsonl(tmp_path / "agent_overlays.jsonl")
    assert "evidence_strength" not in raw
    assert overlays[0]["writeback_allowed"] is False
    assert overlays[0]["review_required"] is True
    assert "unsupported_by_strong_evidence" in overlays[0]["reasons"]
    assert (tmp_path / "grounding_trace.jsonl").exists()
    assert read_jsonl(tmp_path / "review_items.jsonl")


def test_grounding_blocks_answered_without_source(tmp_path: Path) -> None:
    runner = make_runner(
        tmp_path,
        answer_caller=answered_without_source_caller,
        grounding_enabled=True,
        config_overrides={"agentscope": {"enabled": False, "mode": "off"}},
    )

    runner.run([make_item(4)])

    raw = read_jsonl(tmp_path / "predictions_raw.jsonl")[0]
    overlay = read_jsonl(tmp_path / "agent_overlays.jsonl")[0]
    assert raw["answer_status"] == "answered"
    assert raw["source_chunk_ids"] == []
    assert "evidence_strength" not in raw
    assert overlay["writeback_allowed"] is False
    assert overlay["review_required"] is True
    assert overlay["risk_level"] == "high"
    assert "no_valid_evidence_support" in overlay["reasons"]


def test_grounding_never_turns_blocked_overlay_allowed(tmp_path: Path) -> None:
    runner = make_runner(
        tmp_path,
        answer_caller=invalid_source_answer_caller,
        grounding_enabled=True,
        config_overrides={"agentscope": {"enabled": False, "mode": "off"}},
    )

    runner.run([make_item(4)])

    overlay = read_jsonl(tmp_path / "agent_overlays.jsonl")[0]
    assert "invalid_source_reference" in overlay["critic_flags"]
    assert overlay["writeback_allowed"] is False
    assert overlay["review_required"] is True


def test_resume_skips_completed_rows(tmp_path: Path) -> None:
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
    runner = make_runner(tmp_path, answer_caller=answered_answer_caller, retrieval_fn=recording_retrieval(calls), resume=True)

    predictions = runner.run([make_item(4), make_item(5)])

    assert [prediction.row_index for prediction in predictions] == [4, 5]
    assert len(calls) == 1
    assert "skipped_completed_count: 1" in (tmp_path / "run_summary.md").read_text(encoding="utf-8")


def test_field_failure_does_not_abort_run(tmp_path: Path) -> None:
    def caller(**kwargs: Any) -> dict[str, Any]:
        if kwargs["item"]["row_index"] == 5:
            raise RuntimeError("fake llm failure")
        return answered_answer_caller(**kwargs)

    runner = make_runner(tmp_path, answer_caller=caller)

    predictions = runner.run([make_item(4), make_item(5), make_item(6)])

    assert [prediction.row_index for prediction in predictions] == [4, 5, 6]
    failed = next(prediction for prediction in predictions if prediction.row_index == 5)
    assert failed.answer_status == "conflict_unresolved"
    assert failed.validation["error"] == "fake llm failure"
    assert next(prediction for prediction in predictions if prediction.row_index == 6).answer_status == "answered"


def test_judge_uses_raw_prediction(tmp_path: Path) -> None:
    seen_generated_statuses: list[str] = []

    def judge_caller(**kwargs: Any) -> dict[str, Any]:
        seen_generated_statuses.append(kwargs["generated"]["answer_status"])
        return {"label": "mismatch", "score": 0, "reason": "fake"}

    runner = make_runner(tmp_path, answer_caller=not_found_answer_caller, judge_caller=judge_caller, judge_enabled=True)

    runner.run([make_item(4)])

    assert seen_generated_statuses == ["not_found"]
    overlays = read_jsonl(tmp_path / "agent_overlays.jsonl")
    assert overlays[0]["suggested_status"] == "partial_clue"


def test_chat_timeout_retry_success(tmp_path: Path) -> None:
    calls = 0

    def caller(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("curl failed: curl: (28) Operation timed out after 120006 milliseconds with 0 bytes received")
        return answered_answer_caller(**kwargs)

    runner = make_runner(tmp_path, answer_caller=caller, chat_max_retries=2)

    predictions = runner.run([make_item(4)])

    assert calls == 2
    assert predictions[0].answer_status == "answered"
    trace_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "chat_retry_started" in trace_text
    assert "chat_retry_succeeded" in trace_text


def test_chat_timeout_retry_failure_writes_failed_prediction(tmp_path: Path) -> None:
    calls = 0

    def caller(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise RuntimeError("curl failed: curl: (28) Operation timed out after 120006 milliseconds with 0 bytes received")

    runner = make_runner(tmp_path, answer_caller=caller, chat_max_retries=1)

    predictions = runner.run([make_item(4)])

    assert calls == 2
    assert predictions[0].answer_status == "conflict_unresolved"
    assert "timed out" in predictions[0].validation["error"]
    trace_text = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "chat_retry_started" in trace_text
    assert "chat_retry_failed" in trace_text


def test_cli_run_step15_agent_args(tmp_path: Path) -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "run-step15-agent",
            "--config",
            "config/local.example.yaml",
            "--target-namespace",
            "xixian_4",
            "--global-namespace",
            "global",
            "--room-context",
            "西咸4号楼 301机房",
            "--rows",
            "4-5",
            "--retrieval-mode",
            "dense",
            "--grounding-enabled",
            "--retrieval-plan",
            "layered",
            "--out-dir",
            str(tmp_path),
            "--resume",
            "--judge",
            "--chat-max-retries",
            "3",
            "--chat-retry-backoff-seconds",
            "0",
            "--use-judge-cache",
            "--judge-cache",
            str(tmp_path / "judge_cache.jsonl"),
        ]
    )

    assert args.command == "run-step15-agent"
    assert args.rows == "4-5"
    assert args.retrieval_mode == "dense"
    assert args.grounding_enabled is True
    assert args.retrieval_plan == "layered"
    assert args.judge is True
    assert args.resume is True
    assert args.chat_max_retries == 3
    assert args.chat_retry_backoff_seconds == 0
    assert args.prompt_version == "step15_compat"
    assert args.use_judge_cache is True
    assert args.judge_cache == tmp_path / "judge_cache.jsonl"


def test_cli_dense_mode_resolves_to_layered_plan(tmp_path: Path) -> None:
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")

    assert resolve_step15_retrieval_plan("dense", None, config) == "layered"
    assert resolve_step15_retrieval_plan("dense", "flat", config) == "flat"


def test_prompt_version_default_step15_compat() -> None:
    parser = build_parser()

    args = parser.parse_args(["run-step15-agent", "--out-dir", "artifacts/runs/test"])

    assert args.prompt_version == "step15_compat"


def test_regression_rows_config_loads() -> None:
    path = Path("experiments/step15_agent_regression_rows.yaml")
    rows = load_simple_rows_yaml(path)

    assert rows["improved_rows"] == [20, 38, 46, 132, 135, 140]
    assert rows["regressed_rows"] == [14, 57, 102, 124, 130]
    assert rows["timeout_rows"] == [33]


def test_judge_disabled_mode(tmp_path: Path) -> None:
    def judge_caller(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("judge should not be called")

    runner = make_runner(tmp_path, answer_caller=answered_answer_caller, judge_caller=judge_caller, judge_enabled=False)

    runner.run([make_item(4, include_heldout=False)])

    assert (tmp_path / "predictions.jsonl").exists()
    assert (tmp_path / "trace.jsonl").exists()
    assert not (tmp_path / "eval_results.jsonl").exists()


def test_writeback_optional(tmp_path: Path) -> None:
    calls: list[str] = []
    template = tmp_path / "template.xlsx"
    template.write_text("fake", encoding="utf-8")

    runner = make_runner(tmp_path / "no_writeback", answer_caller=answered_answer_caller, writeback_enabled=False, writeback_fn=fake_writeback(calls))
    runner.run([make_item(4)])
    assert calls == []

    runner = make_runner(
        tmp_path / "with_writeback",
        answer_caller=answered_answer_caller,
        writeback_enabled=True,
        template_path=template,
        writeback_fn=fake_writeback(calls),
    )
    runner.run([make_item(4)])
    assert calls == ["called"]


def test_writeback_uses_overlay_gating(tmp_path: Path) -> None:
    captured: list[int] = []
    template = tmp_path / "template.xlsx"
    template.write_text("fake", encoding="utf-8")

    runner = make_runner(
        tmp_path,
        answer_caller=liquid_mismatch_answer_caller,
        writeback_enabled=True,
        template_path=template,
        writeback_fn=capturing_writeback(captured),
    )

    predictions = runner.run([make_item(57, question_text="液冷机柜是否支持")])

    assert predictions[0].answer_status == "answered"
    assert captured == [0]
    overlays = read_jsonl(tmp_path / "agent_overlays.jsonl")
    assert overlays[0]["writeback_allowed"] is False
    assert overlays[0]["suggested_status"] == "partial_clue"
    review_items = read_jsonl(tmp_path / "review_items.jsonl")
    assert review_items
    assert review_items[0]["field_id"] == "item_57"


def test_judge_cache_reuses_same_answer(tmp_path: Path) -> None:
    calls = 0
    cache_path = tmp_path / "judge_cache.jsonl"

    def judge_caller(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"label": "exact", "score": 1, "reason": "fake cached judge"}

    runner = make_runner(
        tmp_path / "run1",
        answer_caller=answered_answer_caller,
        judge_caller=judge_caller,
        judge_enabled=True,
        judge_cache_path=cache_path,
        use_judge_cache=True,
    )
    runner.run([make_item(4)])

    runner = make_runner(
        tmp_path / "run2",
        answer_caller=answered_answer_caller,
        judge_caller=judge_caller,
        judge_enabled=True,
        judge_cache_path=cache_path,
        use_judge_cache=True,
    )
    runner.run([make_item(4)])

    assert calls == 1
    assert len(read_jsonl(cache_path)) == 1


def make_runner(
    tmp_path: Path,
    *,
    answer_caller,
    retrieval_fn=None,
    judge_caller=None,
    judge_enabled: bool = False,
    resume: bool = False,
    writeback_enabled: bool = False,
    template_path: Path | None = None,
    writeback_fn=None,
    chat_max_retries: int = 2,
    prompt_version: str = "step15_compat",
    judge_cache_path: Path | None = None,
    use_judge_cache: bool = False,
    grounding_enabled: bool | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> Step15AgentRunner:
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml", cli_overrides=config_overrides)
    return Step15AgentRunner(
        config=config,
        target_namespace="xixian_4",
        global_namespace="global",
        room_context="西咸4号楼 301机房",
        out_dir=tmp_path,
        retrieval_mode="layered",
        judge_enabled=judge_enabled,
        resume=resume,
        writeback_enabled=writeback_enabled,
        template_path=template_path,
        retrieval_fn=retrieval_fn or fake_retrieval,
        answer_caller=answer_caller,
        judge_caller=judge_caller,
        writeback_fn=writeback_fn or fake_writeback([]),
        chat_max_retries=chat_max_retries,
        chat_retry_backoff_seconds=0,
        prompt_version=prompt_version,
        judge_cache_path=judge_cache_path,
        use_judge_cache=use_judge_cache,
        grounding_enabled=grounding_enabled,
    )


def make_item(row: int, *, include_heldout: bool = True, question_text: str = "市电进线情况") -> dict[str, Any]:
    item = {
        "form_item_id": f"item_{row}",
        "file_name": "基地云机房信息调研表.xlsx",
        "sheet_name": "Sheet1",
        "row_index": row,
        "target_cell": f"D{row}",
        "category_path": ["电力", "市电"],
        "question_text": question_text,
        "instruction_text": "填写市电路数及来源",
        "answer_example": "2路市电",
        "needs_evidence": True,
    }
    if include_heldout:
        item["existing_value"] = "2路市电"
    return item


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


def fake_retrieval_without_answer_value(query: str) -> Step15RetrievalResult:
    del query
    hits = [
        {
            **make_hits()[0],
            "raw_text": "市电进线情况：2路市电，来自同一变电站。",
            "text_for_embedding": "市电进线情况 2路市电",
        }
    ]
    return Step15RetrievalResult(reranked_hits=hits, vector_hits=hits, retrieval_mode="layered")


def recording_retrieval(calls: list[str]):
    def retrieve(query: str) -> Step15RetrievalResult:
        calls.append(query)
        return fake_retrieval(query)

    return retrieve


def answered_answer_caller(**kwargs: Any) -> dict[str, Any]:
    chunk_id = kwargs["hits"][0]["chunk_id"]
    return {
        "answer_value": "2路市电，来自同一变电站",
        "answer_status": "answered",
        "confidence": 0.86,
        "source_chunk_ids": [chunk_id],
        "evidence_attachment_ids": ["att_1"],
        "reference_source_documents": [],
        "agent_resolution": {"used": True, "action": "select_source", "reason": "主表证据直接命中"},
    }


def not_found_answer_caller(**kwargs: Any) -> dict[str, Any]:
    del kwargs
    return {
        "answer_value": "未找到",
        "answer_status": "not_found",
        "confidence": 0.2,
        "source_chunk_ids": [],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
    }


def answered_without_source_caller(**kwargs: Any) -> dict[str, Any]:
    del kwargs
    return {
        "answer_value": "2路市电，来自同一变电站",
        "answer_status": "answered",
        "confidence": 0.86,
        "source_chunk_ids": [],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
    }


def liquid_mismatch_answer_caller(**kwargs: Any) -> dict[str, Any]:
    return {
        "answer_value": "支持",
        "answer_status": "answered",
        "confidence": 0.86,
        "source_chunk_ids": [kwargs["hits"][0]["chunk_id"]],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
    }


def unsupported_answer_caller(**kwargs: Any) -> dict[str, Any]:
    return {
        "answer_value": "500kVA",
        "answer_status": "answered",
        "confidence": 0.86,
        "source_chunk_ids": [kwargs["hits"][0]["chunk_id"]],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
    }


def invalid_source_answer_caller(**kwargs: Any) -> dict[str, Any]:
    del kwargs
    return {
        "answer_value": "2路市电",
        "answer_status": "answered",
        "confidence": 0.86,
        "source_chunk_ids": ["missing_chunk"],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
    }


def fake_writeback(calls: list[str]):
    class Summary:
        def to_dict(self) -> dict[str, Any]:
            return {"output_path": "fake.xlsx"}

    def writeback(**kwargs: Any) -> Summary:
        del kwargs
        calls.append("called")
        return Summary()

    return writeback


def capturing_writeback(captured_counts: list[int]):
    class Summary:
        def to_dict(self) -> dict[str, Any]:
            return {"output_path": "fake.xlsx"}

    def writeback(**kwargs: Any) -> Summary:
        captured_counts.append(len(kwargs["predictions"]))
        output_path = Path(kwargs["output_path"])
        write_jsonl(output_path.parent / "review_items.jsonl", [])
        return Summary()

    return writeback


def load_simple_rows_yaml(path: Path) -> dict[str, list[int]]:
    rows: dict[str, list[int]] = {}
    current: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith(":"):
            current = line[:-1]
            rows[current] = []
            continue
        if line.startswith("-") and current:
            rows[current].append(int(line.removeprefix("-").strip()))
    return rows


def load_json_file(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


class FakeStep15Embedder:
    def embed_query(self, query: str) -> list[float]:
        del query
        return [0.1, 0.2]


class FakeStep15DenseRetriever:
    collection_name = "fake_collection"

    def __init__(self) -> None:
        self.embedder = FakeStep15Embedder()

    def search_by_vector(
        self,
        vector: list[float],
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        del vector, source_types, top_k
        if namespaces == ["xixian_4"] and layers == ["fact"]:
            return [dense_record("chunk_main", "市电进线情况：2路市电，来自同一变电站。")]
        return []


class FakeStep15Reranker:
    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
        del query
        return [{"index": index, "relevance_score": 1.0 - index * 0.01} for index in range(min(top_n, len(documents)))]


def step15_layer_spec() -> dict[str, Any]:
    return {
        "layer_name": "target_main_fact",
        "description": "main",
        "namespaces": "target",
        "corpus_layers": ["fact"],
        "source_types": ["main_excel_capability"],
        "vector_top_k": 3,
        "rerank_top_n": 3,
    }


def dense_record(chunk_id: str, raw_text: str) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "namespace": "xixian_4",
        "source_type": "main_excel_capability",
        "corpus_layer": "fact",
        "retrieval_layer": "target_main_fact",
        "raw_text": raw_text,
        "text_for_embedding": raw_text,
        "vector_rank": 1,
        "vector_score": 0.9,
    }
