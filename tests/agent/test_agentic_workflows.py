from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from conftest import (
    SequenceAnswer,
    SequenceRetrieval,
    agentic_config,
    answer_json,
    make_agentic_runner,
    make_hit,
    make_item,
    workflows,
)

from nested_doc_rag.io import read_json, read_jsonl


def test_all_workflow_switches_off_stops_after_one_arbitration(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")]])
    answer = SequenceAnswer(
        [
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="台数", failure_modes=["slot_missing"]),
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="台数", failure_modes=["slot_missing"]),
        ]
    )
    config = agentic_config(
        make_agentic_runner_config(tmp_path),
        workflows_config=workflows(
            missing_info=False,
            wrong_answer_risk=False,
            not_found_recovery=False,
            uncertainty_conflict=False,
        ),
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer, mas_config=config)

    runner.run([make_item()])

    summary = read_json(tmp_path / "agentic_summary.json")
    trace = read_jsonl(tmp_path / "agentic_mas_trace.jsonl")
    assert summary["fields"][0]["rounds"] == 1
    assert summary["fields"][0]["retrieval_actions"] == 0
    assert summary["fields"][0]["stopped_reason"].startswith("mark_unresolved")
    assert "slot_targeted_retrieval" not in action_types(trace)


def test_missing_info_triggers_slot_targeted_retrieval(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")], [make_hit("initial"), make_hit("slot_direct")]])
    answer = SequenceAnswer(
        [
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="进线来源", failure_modes=["slot_missing"]),
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="进线来源", failure_modes=["slot_missing"]),
            answer_json(
                status="answered",
                value="2路市电，来自同一变电站",
                state_kind="sufficient",
                source_chunk_ids=["slot_direct"],
                confidence=0.9,
            ),
        ]
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer)

    predictions = runner.run([make_item()])

    trace = read_jsonl(tmp_path / "agentic_mas_trace.jsonl")
    assert predictions[0].answer_status == "answered"
    assert "slot_targeted_retrieval" in action_types(trace)
    assert len(retrieval.calls) == 2


def test_not_found_recovery_triggers_alias_layer_and_source_specific_retrieval(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval(
        [
            [make_hit("initial")],
            [make_hit("alias")],
            [make_hit("layer")],
            [make_hit("source")],
        ]
    )
    answer = SequenceAnswer(
        [
            answer_json(status="not_found", state_kind="not_found_recovery", failure_modes=["evidence_absence"]),
            answer_json(status="not_found", state_kind="not_found_recovery", failure_modes=["evidence_absence"]),
            answer_json(status="answered", value="2路市电", state_kind="sufficient", source_chunk_ids=["alias"], confidence=0.91),
        ]
    )
    base_config = make_agentic_runner_config(tmp_path)
    config = replace(agentic_config(base_config), max_actions_per_round=3)
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer, mas_config=config)

    runner.run([make_item()])

    types = action_types(read_jsonl(tmp_path / "agentic_mas_trace.jsonl"))
    assert {"alias_retrieval", "layer_expansion", "source_specific_retrieval"}.issubset(types)


def test_wrong_answer_risk_triggers_contrastive_retrieval(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")], [make_hit("correcting")]])
    answer = SequenceAnswer(
        [
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="市电路数", failure_modes=["slot_missing"]),
            answer_json(
                status="answered",
                value="1路市电",
                state_kind="wrong_answer_risk",
                failure_modes=["weak_grounding"],
                source_chunk_ids=["initial"],
                confidence=0.6,
                risk_reason="weak grounding",
            ),
            answer_json(status="answered", value="2路市电", state_kind="sufficient", source_chunk_ids=["correcting"], confidence=0.92),
        ]
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer)

    runner.run([make_item()])

    assert "contrastive_retrieval" in action_types(read_jsonl(tmp_path / "agentic_mas_trace.jsonl"))


def test_uncertainty_conflict_triggers_disambiguation_and_candidate_challenges(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")], [make_hit("disambiguation")], [make_hit("challenge")]])
    answer = SequenceAnswer(
        [
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="市电路数", failure_modes=["slot_missing"]),
            answer_json(
                status="conflict_unresolved",
                state_kind="uncertainty_conflict",
                failure_modes=["candidate_conflict"],
                candidates=["1路市电", "2路市电"],
            ),
            answer_json(status="answered", value="2路市电", state_kind="sufficient", source_chunk_ids=["disambiguation"], confidence=0.93),
        ]
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer)

    runner.run([make_item()])

    types = action_types(read_jsonl(tmp_path / "agentic_mas_trace.jsonl"))
    assert "disambiguation_retrieval" in types
    assert "contrastive_retrieval" in types


def test_no_novel_chunks_stops_with_reason(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")], [make_hit("initial")]])
    answer = SequenceAnswer(
        [
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="台数", failure_modes=["slot_missing"]),
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="台数", failure_modes=["slot_missing"]),
        ]
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer)

    runner.run([make_item()])

    summary = read_json(tmp_path / "agentic_summary.json")
    assert summary["fields"][0]["stopped_reason"] == "no_novel_chunks"
    assert summary["fields"][0]["rounds"] == 1


def test_final_overlay_uses_final_round_prediction(tmp_path: Path) -> None:
    initial_hit = make_hit("initial", "市电进线情况：待核实")
    final_hit = {
        **make_hit("final", "市电进线情况：最终答案"),
        "evidence_kind": "structured_field", "knowledge_base_id": "fixture-main-kb",
        "sheet_name": "能力清单", "row_index": 12, "cell_range": "A12:C12",
        "raw_source_text": "市电进线情况：最终答案",
        "field_name": "市电进线情况", "field_value": "最终答案",
    }
    retrieval = SequenceRetrieval([[initial_hit], [final_hit]])
    answer = SequenceAnswer(
        [
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="来源", failure_modes=["slot_missing"]),
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="来源", failure_modes=["slot_missing"]),
            answer_json(
                status="answered",
                value="最终答案",
                state_kind="sufficient",
                source_chunk_ids=["final"],
                confidence=0.95,
            ),
        ]
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer)

    runner.run([make_item()])

    raw = read_jsonl(tmp_path / "predictions_raw.jsonl")[0]
    overlay = read_jsonl(tmp_path / "agent_overlays.jsonl")[0]
    assert raw["answer_value"] == "最终答案"
    assert raw["answer_status"] == "answered"
    assert overlay["writeback_allowed"] is True


def make_agentic_runner_config(tmp_path: Path):
    config = make_agentic_runner(
        tmp_path / "config_probe",
        retrieval=SequenceRetrieval([[make_hit("probe")]]),
        answer=SequenceAnswer([answer_json(status="answered", value="probe", state_kind="sufficient", source_chunk_ids=["probe"], confidence=0.9)]),
    ).config
    return config.agentic_mas


def action_types(trace: list[dict]) -> set[str]:
    types: set[str] = set()
    for event in trace:
        payload = event.get("payload") or {}
        for action in payload.get("actions") or []:
            if action.get("action_type"):
                types.add(action["action_type"])
        action = payload.get("action") or {}
        if action.get("action_type"):
            types.add(action["action_type"])
    return types
