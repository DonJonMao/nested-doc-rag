from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from nested_doc_rag.agent.mas.agentscope_bridge import build_agentscope_runtime
from nested_doc_rag.agent.mas.schemas import EvidenceScoutReport, QueryPlan
from nested_doc_rag.agent.mas.supplemental import SupplementalRetrievalGate
from nested_doc_rag.agent.step15_runner import Step15AgentRunner
from nested_doc_rag.artifacts import validate_step15_artifacts
from nested_doc_rag.config import AgentScopeConfig, MASConfig, load_app_config
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
    assert events[0]["mode"] == "equivalent_mas"
    assert events[0]["agentscope_available"] is False
    assert [event["role"] for event in events if event.get("event_type") == "role_bypassed"] == [
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


def test_default_config_stays_off_without_mas_artifacts(tmp_path: Path) -> None:
    off_dir = tmp_path / "off"
    default_dir = tmp_path / "default"

    make_runner(off_dir, mode="off").run([make_item(4)])
    make_default_runner(default_dir).run([make_item(4)])

    assert_core_artifacts_equal(off_dir, default_dir)
    assert not (default_dir / "mas_trace.jsonl").exists()
    assert not (default_dir / "agentscope_events.jsonl").exists()


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


def test_enhanced_mas_supplemental_retrieval_can_rescue_not_found(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[]),
        answer_caller=supplemental_answer_caller,
    )

    predictions = runner.run([make_item(20, question_text="补充证据字段")])

    assert predictions[0].answer_status == "answered"
    assert predictions[0].source_chunk_ids == ["chunk_supplemental"]
    assert predictions[0].validation["source_ids_valid"] is True
    assert len(calls) == 2
    assert read_jsonl(tmp_path / "query_plans.jsonl")[0]["primary_query"]
    scout = read_jsonl(tmp_path / "evidence_scout_reports.jsonl")[0]
    assert scout["evidence_sufficient"] is False
    supplemental = read_jsonl(tmp_path / "supplemental_retrievals.jsonl")[0]
    assert supplemental["enabled"] is True
    assert supplemental["baseline_hit_count"] == 0
    assert supplemental["final_hit_count"] == 1
    overlay = read_jsonl(tmp_path / "agent_overlays.jsonl")[0]
    assert overlay["writeback_allowed"] is True
    assert validate_step15_artifacts(tmp_path)["valid"] is True


def test_enhanced_mas_does_not_supplement_baseline_answered_by_default(tmp_path: Path) -> None:
    calls: list[str] = []
    off_dir = tmp_path / "off"
    enhanced_dir = tmp_path / "enhanced"

    make_runner(off_dir, mode="off", retrieval_fn=recording_retrieval(calls), answer_caller=answer_caller).run([make_item(4)])
    calls.clear()
    make_runner(enhanced_dir, mode="enhanced_mas", retrieval_fn=recording_retrieval(calls), answer_caller=answer_caller).run([make_item(4)])

    assert_core_artifacts_equal(off_dir, enhanced_dir)
    assert len(calls) == 1
    supplemental = read_jsonl(enhanced_dir / "supplemental_retrievals.jsonl")[0]
    assert supplemental["enabled"] is False
    assert supplemental["reason"] == "baseline_answered"
    adoption = read_jsonl(enhanced_dir / "adoption_decisions.jsonl")[0]
    assert adoption["baseline_status"] == "answered"
    assert adoption["enhanced_candidate_status"] == "answered"
    assert adoption["adopted_status"] == "answered"
    assert adoption["adoption_decision"] == "kept_baseline"


def test_enhanced_mas_partial_clue_is_not_downgraded_to_not_found(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[irrelevant_hit()]),
        answer_caller=partial_then_not_found_answer_caller(),
    )

    predictions = runner.run([make_item(23, question_text="补充证据字段")])

    assert predictions[0].answer_status == "partial_clue"
    assert len(calls) == 2
    adoption = read_jsonl(tmp_path / "adoption_decisions.jsonl")[0]
    assert adoption["baseline_status"] == "partial_clue"
    assert adoption["enhanced_candidate_status"] == "not_found"
    assert adoption["adopted_status"] == "partial_clue"
    assert adoption["adoption_reason"] == "partial_clue_allows_answered_only"


def test_enhanced_mas_partial_clue_is_not_downgraded_to_conflict(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[irrelevant_hit()]),
        answer_caller=partial_then_conflict_answer_caller(),
    )

    predictions = runner.run([make_item(24, question_text="补充证据字段")])

    assert predictions[0].answer_status == "partial_clue"
    adoption = read_jsonl(tmp_path / "adoption_decisions.jsonl")[0]
    assert adoption["enhanced_candidate_status"] == "conflict_unresolved"
    assert adoption["adopted_status"] == "partial_clue"


def test_enhanced_mas_not_found_can_promote_to_partial_clue(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[]),
        answer_caller=not_found_then_partial_answer_caller(),
    )

    predictions = runner.run([make_item(25, question_text="补充证据字段")])

    assert predictions[0].answer_status == "partial_clue"
    assert len(calls) == 2
    adoption = read_jsonl(tmp_path / "adoption_decisions.jsonl")[0]
    assert adoption["baseline_status"] == "not_found"
    assert adoption["enhanced_candidate_status"] == "partial_clue"
    assert adoption["adopted_status"] == "partial_clue"
    assert adoption["adoption_decision"] == "adopted"


def test_enhanced_mas_conflict_unresolved_does_not_trigger_supplemental_retrieval(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[]),
        answer_caller=conflict_answer_caller,
    )

    predictions = runner.run([make_item(26, question_text="补充证据字段")])

    assert predictions[0].answer_status == "conflict_unresolved"
    assert len(calls) == 1
    supplemental = read_jsonl(tmp_path / "supplemental_retrievals.jsonl")[0]
    assert supplemental["enabled"] is False
    assert supplemental["reason"] == "baseline_conflict_unresolved"


def test_supplemental_gate_does_not_allow_evidence_gap_without_status_eligibility() -> None:
    gate = SupplementalRetrievalGate(MASConfig(enabled=True, mode="enhanced_mas"))
    plan = gate.plan(
        item=make_item(27, question_text="补充证据字段"),
        query_plan=QueryPlan(
            base_query="base",
            query_text="primary",
            primary_query="primary",
            fallback_queries=["fallback"],
            evidence_slots=["missing slot"],
            answer_constraints=[],
            preferred_layers=[],
            source_constraints=[],
        ),
        scout_report=EvidenceScoutReport(
            field_id="item_27",
            evidence_sufficient=False,
            missing_slots=["missing slot"],
            conflict_suspected=False,
            supplemental_queries=["fallback"],
            rationale="missing",
        ),
        baseline_status="manual_review",
        baseline_critic_flags=[],
    )

    assert plan.enabled is False
    assert plan.reason == "baseline_status_not_eligible"


def test_supplemental_gate_zero_rounds_disables_supplemental_retrieval() -> None:
    gate = SupplementalRetrievalGate(MASConfig(enabled=True, mode="enhanced_mas", max_supplemental_rounds=0))
    plan = gate.plan(
        item=make_item(28, question_text="补充证据字段"),
        query_plan=QueryPlan(
            base_query="base",
            query_text="primary",
            primary_query="primary",
            fallback_queries=["fallback"],
            evidence_slots=["missing slot"],
            answer_constraints=[],
            preferred_layers=[],
            source_constraints=[],
        ),
        scout_report=EvidenceScoutReport(
            field_id="item_28",
            evidence_sufficient=False,
            missing_slots=["missing slot"],
            conflict_suspected=False,
            supplemental_queries=["fallback"],
            rationale="missing",
        ),
        baseline_status="not_found",
        baseline_critic_flags=[],
    )

    assert plan.enabled is False
    assert plan.reason == "max_supplemental_rounds_zero"


def test_enhanced_mas_supplemental_hits_append_after_baseline(tmp_path: Path) -> None:
    seen_hit_orders: list[list[str]] = []

    def caller(**kwargs: Any) -> dict[str, Any]:
        seen_hit_orders.append([str(hit.get("chunk_id")) for hit in kwargs["hits"]])
        if any(hit.get("chunk_id") == "chunk_supplemental" for hit in kwargs["hits"]):
            return {
                "answer_value": "补充命中答案",
                "answer_status": "answered",
                "confidence": 0.82,
                "source_chunk_ids": ["chunk_supplemental"],
                "evidence_attachment_ids": [],
                "reference_source_documents": [],
            }
        return {
            "answer_value": "未找到",
            "answer_status": "not_found",
            "confidence": 0.2,
            "source_chunk_ids": [],
            "evidence_attachment_ids": [],
            "reference_source_documents": [],
        }

    calls: list[str] = []
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[irrelevant_hit()]),
        answer_caller=caller,
    )

    runner.run([make_item(21, question_text="补充证据字段")])

    assert len(calls) == 2
    assert seen_hit_orders[-1] == ["chunk_irrelevant", "chunk_supplemental"]
    supplemental = read_jsonl(tmp_path / "supplemental_retrievals.jsonl")[0]
    assert supplemental["baseline_hit_count"] == 1
    assert supplemental["final_hit_count"] == 2


def test_enhanced_mas_invalid_supplemental_source_id_is_blocked(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[]),
        answer_caller=invalid_source_answer_caller,
    )

    predictions = runner.run([make_item(22, question_text="补充证据字段")])

    assert predictions[0].answer_status == "not_found"
    assert predictions[0].validation["source_ids_valid"] is True
    overlay = read_jsonl(tmp_path / "agent_overlays.jsonl")[0]
    assert overlay["writeback_allowed"] is False
    assert overlay["review_required"] is True
    adoption = read_jsonl(tmp_path / "adoption_decisions.jsonl")[0]
    assert adoption["baseline_status"] == "not_found"
    assert adoption["enhanced_candidate_status"] == "answered"
    assert adoption["adoption_decision"] == "kept_baseline"
    assert adoption["adoption_reason"] == "candidate_answered_failed_target_or_safety"


def test_enhanced_mas_adoption_policy_preserves_required_artifact_contract(tmp_path: Path) -> None:
    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval([], baseline_hits=[]),
        answer_caller=supplemental_answer_caller,
    )

    runner.run([make_item(29, question_text="补充证据字段")])

    assert read_jsonl(tmp_path / "predictions_raw.jsonl") == read_jsonl(tmp_path / "predictions.jsonl")
    assert read_jsonl(tmp_path / "adoption_decisions.jsonl")
    manifest = read_json(tmp_path / "run_manifest.json")
    assert manifest["artifacts"]["predictions_raw"] == "predictions_raw.jsonl"
    assert manifest["artifacts"]["agent_overlays"] == "agent_overlays.jsonl"
    assert manifest["artifacts"]["review_items"] == "review_items.jsonl"
    assert manifest["artifacts"]["adoption_decisions"] == "adoption_decisions.jsonl"
    assert validate_step15_artifacts(tmp_path)["valid"] is True


def test_enhanced_mas_resume_skips_completed_checkpoint(tmp_path: Path) -> None:
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

    runner = make_runner(
        tmp_path,
        mode="enhanced_mas",
        retrieval_fn=supplemental_retrieval(calls, baseline_hits=[]),
        answer_caller=supplemental_answer_caller,
        resume=True,
    )
    predictions = runner.run([make_item(4), make_item(5, question_text="补充证据字段")])

    assert [prediction.row_index for prediction in predictions] == [4, 5]
    assert len(calls) == 2
    assert validate_step15_artifacts(tmp_path)["valid"] is True


def test_agentscope_disabled_runtime_falls_back_to_local_roles() -> None:
    runtime = build_agentscope_runtime(enabled=False)

    assert runtime.available is False
    assert runtime.run_role("query_planner", {}, lambda: {"ok": True}) == {"ok": True}
    assert runtime.events[0]["event_type"] == "role_bypassed"


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
    answer_caller: Callable[..., dict[str, Any]] | None = None,
    resume: bool = False,
) -> Step15AgentRunner:
    config = load_app_config(project_root=out_dir, default_config=out_dir / "missing.yaml")
    config = replace(
        config,
        agentscope=AgentScopeConfig(enabled=False, mode="off"),
        mas=MASConfig(enabled=mode != "off", mode=mode),
    )
    return Step15AgentRunner(
        config=config,
        target_namespace="xixian_4",
        global_namespace="global",
        room_context="西咸4号楼 301机房",
        out_dir=out_dir,
        retrieval_mode="layered",
        retrieval_fn=retrieval_fn or fake_retrieval,
        answer_caller=answer_caller or globals()["answer_caller"],
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
        retrieval_mode="layered",
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


def supplemental_answer_caller(**kwargs: Any) -> dict[str, Any]:
    hits = kwargs["hits"]
    if not hits:
        return {
            "answer_value": "未找到",
            "answer_status": "not_found",
            "confidence": 0.2,
            "source_chunk_ids": [],
            "evidence_attachment_ids": [],
            "reference_source_documents": [],
        }
    source_id = hits[-1]["chunk_id"]
    return {
        "answer_value": "补充命中答案",
        "answer_status": "answered",
        "confidence": 0.84,
        "source_chunk_ids": [source_id],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
        "agent_resolution": {"used": True, "action": "select_source", "reason": "补充证据命中"},
    }


def invalid_source_answer_caller(**kwargs: Any) -> dict[str, Any]:
    if not kwargs["hits"]:
        return {
            "answer_value": "未找到",
            "answer_status": "not_found",
            "confidence": 0.2,
            "source_chunk_ids": [],
            "evidence_attachment_ids": [],
            "reference_source_documents": [],
        }
    return {
        "answer_value": "补充命中答案",
        "answer_status": "answered",
        "confidence": 0.84,
        "source_chunk_ids": ["not_in_hits"],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
    }


def partial_then_not_found_answer_caller() -> Callable[..., dict[str, Any]]:
    def caller(**kwargs: Any) -> dict[str, Any]:
        if any(hit.get("chunk_id") == "chunk_supplemental" for hit in kwargs["hits"]):
            return {
                "answer_value": "未找到",
                "answer_status": "not_found",
                "confidence": 0.2,
                "source_chunk_ids": [],
                "evidence_attachment_ids": [],
                "reference_source_documents": [],
            }
        return partial_answer(kwargs["hits"][0]["chunk_id"])

    return caller


def partial_then_conflict_answer_caller() -> Callable[..., dict[str, Any]]:
    def caller(**kwargs: Any) -> dict[str, Any]:
        if any(hit.get("chunk_id") == "chunk_supplemental" for hit in kwargs["hits"]):
            return {
                "answer_value": "存在冲突，请人工复核",
                "answer_status": "conflict_unresolved",
                "confidence": 0.3,
                "source_chunk_ids": [],
                "evidence_attachment_ids": [],
                "reference_source_documents": [],
            }
        return partial_answer(kwargs["hits"][0]["chunk_id"])

    return caller


def not_found_then_partial_answer_caller() -> Callable[..., dict[str, Any]]:
    def caller(**kwargs: Any) -> dict[str, Any]:
        if any(hit.get("chunk_id") == "chunk_supplemental" for hit in kwargs["hits"]):
            return partial_answer("chunk_supplemental")
        return {
            "answer_value": "未找到",
            "answer_status": "not_found",
            "confidence": 0.2,
            "source_chunk_ids": [],
            "evidence_attachment_ids": [],
            "reference_source_documents": [],
        }

    return caller


def conflict_answer_caller(**kwargs: Any) -> dict[str, Any]:
    del kwargs
    return {
        "answer_value": "存在冲突，请人工复核",
        "answer_status": "conflict_unresolved",
        "confidence": 0.3,
        "source_chunk_ids": [],
        "evidence_attachment_ids": [],
        "reference_source_documents": [],
    }


def partial_answer(chunk_id: str) -> dict[str, Any]:
    return {
        "answer_value": "检索到相关线索，请人工复核",
        "answer_status": "partial_clue",
        "confidence": 0.45,
        "source_chunk_ids": [],
        "evidence_attachment_ids": [],
        "reference_source_documents": [
            {
                "chunk_id": chunk_id,
                "namespace": "xixian_4",
                "source_type": "embedded_raw_segment",
                "corpus_layer": "raw_text",
                "retrieval_layer": "target_raw_detail",
                "reason": "partial clue",
            }
        ],
    }


def supplemental_retrieval(calls: list[str], *, baseline_hits: list[dict[str, Any]]) -> Callable[[str], Step15RetrievalResult]:
    def retrieve(query: str) -> Step15RetrievalResult:
        calls.append(query)
        if "任务：" in query:
            hits = list(baseline_hits)
        else:
            hits = [supplemental_hit()]
        return Step15RetrievalResult(reranked_hits=hits, vector_hits=hits, retrieval_mode="layered")

    return retrieve


def supplemental_hit() -> dict[str, Any]:
    return {
        "chunk_id": "chunk_supplemental",
        "namespace": "xixian_4",
        "source_type": "embedded_word_table",
        "corpus_layer": "fact",
        "retrieval_layer": "target_structured_detail",
        "layer_priority": 2,
        "rerank_score": 0.88,
        "file_name": "detail.docx",
        "anchor": "table 1",
        "raw_text": "补充证据字段：补充命中答案。",
        "text_for_embedding": "补充证据字段 补充命中答案",
    }


def irrelevant_hit() -> dict[str, Any]:
    return {
        "chunk_id": "chunk_irrelevant",
        "namespace": "xixian_4",
        "source_type": "embedded_raw_segment",
        "corpus_layer": "raw_text",
        "retrieval_layer": "target_raw_detail",
        "layer_priority": 3,
        "rerank_score": 0.3,
        "file_name": "raw.docx",
        "anchor": "P9",
        "raw_text": "这里只是无关背景。",
        "text_for_embedding": "无关背景",
    }
