from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from nested_doc_rag.agent.step15_runner import Step15AgentRunner, build_acquisition_metrics
from nested_doc_rag.config import load_app_config
from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult
from nested_doc_rag.io import read_json, read_jsonl


def item() -> dict:
    return {"form_item_id": "ups", "file_name": "new.xlsx", "sheet_name": "调研", "row_index": 2,
            "target_cell": "'调研'!D2", "question_text": "UPS配置", "instruction_text": "填写品牌、容量",
            "category_path": ["动力", "UPS"], "answer_example": "", "needs_evidence": True}


def hit(identifier: str = "brand", *, text: str = "UPS品牌：维谛", kind: str = "structured_field", namespace: str = "room") -> dict:
    return {"chunk_id": identifier, "evidence_id": identifier, "namespace": namespace, "knowledge_base_id": "kb",
            "evidence_kind": kind, "source_type": "uploaded_excel_row" if kind == "structured_field" else "uploaded_docx_paragraph",
            "corpus_layer": "fact" if kind == "structured_field" else "raw_text", "file_name": "knowledge.xlsx" if kind == "structured_field" else "detail.docx",
            "sheet_name": "能力" if kind == "structured_field" else None, "row_index": 2 if kind == "structured_field" else None,
            "cell_range": "A2:B2" if kind == "structured_field" else None, "paragraph_index": 1 if kind != "structured_field" else None,
            "raw_source_text": text, "raw_text": text, "field_name": "UPS品牌" if kind == "structured_field" else None,
            "field_value": "维谛" if kind == "structured_field" else None}


def sufficient(*ids: str, missing: list[str] | None = None) -> dict:
    return {"sufficient": missing is None, "missing_facts": missing or [], "supporting_evidence_ids": list(ids),
            "reason": "当前原文覆盖必需事实" if missing is None else "当前原文缺少必要事实"}


def make_runner(path: Path, packs: list[list[dict]], assessments: list[dict], *, retry: bool = False, enabled: bool = True):
    config = load_app_config(project_root=path, default_config=path / "missing.yaml", env={}, cli_overrides={
        "retrieval": {"sufficiency_enabled": enabled}, "agentscope": {"enabled": False, "mode": "off"},
        "grounding": {"evidence_strength_enabled": False, "field_binding_enabled": False, "field_binding_agent_enabled": False,
                      "slot_decomposition_enabled": False, "pre_writeback_consistency_enabled": False},
    })
    queries, checks, answers = [], [], []

    def retrieve(query):
        queries.append(query)
        if retry and len(queries) == 1:
            raise RuntimeError("connection reset by peer")
        index = min(len(queries) - 1 - int(retry), len(packs) - 1)
        pack = deepcopy(packs[index])
        return Step15RetrievalResult(pack, deepcopy(pack), "layered")

    def assess(**kwargs):
        checks.append(kwargs)
        return deepcopy(assessments[min(len(checks) - 1, len(assessments) - 1)])

    def answer(**kwargs):
        answers.append(kwargs)
        return {"answer_status": "answered", "answer_value": "维谛", "confidence": 0.9, "source_chunk_ids": ["brand"]}

    runner = Step15AgentRunner(config=config, out_dir=path, target_namespace="room", global_namespace="global", room_context="301机房",
                              retrieval_fn=retrieve, sufficiency_caller=assess, answer_caller=answer,
                              chat_max_retries=1 if retry else 0, chat_retry_backoff_seconds=0)
    return runner, queries, checks, answers


def test_primary_sufficient_has_one_acquisition_and_one_arbitration(tmp_path: Path) -> None:
    runner, queries, checks, answers = make_runner(tmp_path, [[hit()]], [sufficient("brand")])
    prediction = runner.run([item()])[0]
    assert len(queries) == len(checks) == len(answers) == 1
    assert prediction.validation["acquisition"]["acquisition_rounds"] == 1
    assert prediction.evidence_refs[0].source_text == "UPS品牌：维谛"
    assert answers[0]["hits"][0]["retrieval_round"] == 0
    metrics = read_json(tmp_path / "summary.json")["retrieval_metrics"]
    assert metrics["second_round_trigger_rate"] == 0 and metrics["qdrant_query_calls"] is None
    assert runner.mas_controller is None


def test_missing_fact_drives_only_one_supplement_and_deduplicates(tmp_path: Path) -> None:
    detail = hit("capacity", text="UPS容量：500kVA", kind="paragraph")
    runner, queries, checks, answers = make_runner(tmp_path, [[hit()], [hit(), detail]], [sufficient("brand", missing=["UPS容量"]), sufficient("brand", "capacity")])
    result = runner.run([item()])[0]
    assert len(queries) == len(checks) == 2 and len(answers) == 1
    assert "缺失事实=UPS容量" in queries[1] and "301机房" in queries[1]
    assert len(answers[0]["hits"]) == 2 and len({entry["chunk_id"] for entry in answers[0]["hits"]}) == 2
    assert {entry["chunk_id"]: entry["retrieval_round"] for entry in answers[0]["hits"]} == {"brand": 0, "capacity": 1}
    assert result.validation["acquisition"]["rounds"][1]["evidence_gain"] == 1
    steps = [event["step"] for event in read_jsonl(tmp_path / "trace.jsonl")]
    assert steps.count("targeted_retrieval_started") == 1 and steps.count("evidence_sufficiency_checked") == 2


@pytest.mark.parametrize("packs", [[[hit()], [hit()]], [[], []]])
def test_still_insufficient_abstains_before_answer_generation(tmp_path: Path, packs) -> None:
    runner, queries, checks, answers = make_runner(tmp_path, packs, [sufficient(missing=["UPS容量"])])
    prediction = runner.run([item()])[0]
    assert len(queries) == len(checks) == 2 and not answers
    assert prediction.answer_status == ("partial_clue" if packs[0] else "not_found")
    assert prediction.validation["step15_generated"]["origin"] == "system_sufficiency_abstention"
    assert not read_jsonl(tmp_path / "agent_overlays.jsonl")[0]["writeback_allowed"]


def test_cross_round_conflicting_same_id_is_observable_and_cannot_answer(tmp_path: Path) -> None:
    runner, queries, checks, answers = make_runner(tmp_path, [[hit()], [hit(text="UPS品牌：不同厂商")]],
                                                  [sufficient("brand", missing=["UPS容量"]), sufficient("brand")])
    prediction = runner.run([item()])[0]
    assert len(queries) == 2 and not answers and prediction.answer_status == "partial_clue"
    acquisition = prediction.validation["acquisition"]
    assert acquisition["conflicting_evidence"][0]["chunk_id"] == "brand"
    assert acquisition["final_sufficiency"]["sufficient"] is False
    assert len(acquisition["conflicting_evidence"][0]["origin_hits"]) == 2


def test_network_retry_is_counted_separately_from_acquisition_rounds(tmp_path: Path) -> None:
    runner, queries, checks, answers = make_runner(tmp_path, [[hit()]], [sufficient("brand")], retry=True)
    prediction = runner.run([item()])[0]
    assert len(queries) == 2 and len(checks) == len(answers) == 1
    assert prediction.validation["acquisition"]["acquisition_rounds"] == 1
    assert prediction.validation["acquisition"]["retrieval_attempts"] == 2


def test_invalid_json_check_fails_closed_and_remains_bounded(tmp_path: Path) -> None:
    runner, queries, _, answers = make_runner(tmp_path, [[hit()]], [sufficient("brand")])

    def invalid(**kwargs):
        raise json.JSONDecodeError("bad JSON", "{", 1)

    runner.sufficiency_caller = invalid
    prediction = runner.run([item()])[0]
    assert len(queries) == 2 and not answers and prediction.answer_status == "partial_clue"
    assert any(diagnostic["reason"] == "json_parse_retries_exhausted" for diagnostic in prediction.validation["acquisition"]["final_sufficiency"]["diagnostics"])


def test_global_evidence_is_excluded_from_primary_pack(tmp_path: Path) -> None:
    global_hit = {**hit("global-brand", namespace="global"), "corpus_layer": "raw_text"}
    runner, _, checks, _ = make_runner(tmp_path, [[global_hit], [hit()]], [sufficient(missing=["目标机房UPS品牌"]), sufficient("brand")])
    runner.run([item()])
    assert checks[0]["hits"] == [] and checks[1]["hits"][0]["namespace"] == "room"


@pytest.mark.parametrize("change", ["enabled", "prompt", "plan"])
def test_changed_strategy_rejects_resume_before_calls_or_writes(tmp_path: Path, change: str) -> None:
    runner, _, _, _ = make_runner(tmp_path, [[hit()]], [sufficient("brand")])
    runner.run([item()])
    previous = {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    resumed, queries, checks, answers = make_runner(tmp_path, [[hit()]], [sufficient("brand")], enabled=change != "enabled")
    resumed.resume = True
    if change == "prompt":
        resumed.prompt_version = "agent_v2"
    elif change == "plan":
        resumed.layered_plan = [{**entry, "vector_top_k": 1} for entry in resumed.layered_plan]
    with pytest.raises(RuntimeError, match="cannot resume"):
        resumed.run([item()])
    assert not queries and not checks and not answers
    assert previous == {str(path.relative_to(tmp_path)): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_sufficiency_and_legacy_mas_cannot_stack(tmp_path: Path) -> None:
    runner, _, _, _ = make_runner(tmp_path, [[hit()]], [sufficient("brand")])
    config = replace(runner.config, agentscope=replace(runner.config.agentscope, enabled=True, mode="agentic_mas"))
    with pytest.raises(ValueError, match="explicitly disable sufficiency"):
        Step15AgentRunner(config=config, out_dir=tmp_path, target_namespace="room", global_namespace="global", retrieval_fn=lambda _: None)


def test_empty_measurements_report_unknown_calls() -> None:
    assert build_acquisition_metrics([])["qdrant_query_calls"] is None


def test_configured_structured_subset_still_retrieves_and_answers(tmp_path: Path) -> None:
    runner, queries, checks, answers = make_runner(tmp_path, [[hit()]], [sufficient("brand")])
    runner.layered_plan = [spec for spec in runner.layered_plan if spec["layer_name"] == "target_structured_fact"]
    prediction = runner.run([item()])[0]
    assert len(queries) == len(checks) == len(answers) == 1
    assert prediction.validation["acquisition"]["acquisition_rounds"] == 1
    assert prediction.evidence_refs[0].chunk_id == "brand"
    assert not prediction.validation["acquisition"]["rounds"][0].get("constraint_rejected")


def test_text_only_subset_skips_empty_primary_without_backend_attempt(tmp_path: Path) -> None:
    detail = hit("brand", text="UPS品牌：维谛", kind="paragraph")
    runner, queries, checks, answers = make_runner(tmp_path, [[detail]], [sufficient(missing=["UPS品牌"]), sufficient("brand")])
    runner.layered_plan = [spec for spec in runner.layered_plan if spec["layer_name"] == "target_text_detail"]
    prediction = runner.run([item()])[0]
    assert len(queries) == len(answers) == 1 and len(checks) == 2
    primary = prediction.validation["acquisition"]["rounds"][0]
    assert primary["qdrant_query_calls"] == primary["retrieval_attempts"] == 0
    assert checks[0]["hits"] == []
    # Model-proposed unknown layers continue to be rejected, never widened.
    rejected = runner.retrieve("UPS", layer_names=["unconfigured"])
    assert rejected.metadata["constraint_rejected"] and rejected.metadata["qdrant_query_calls"] == 0
    assert len(queries) == 1
