from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SequenceAnswer, SequenceRetrieval, agentic_config, answer_json, make_agentic_runner, make_hit, make_item, workflows

from nested_doc_rag.artifacts import validate_step15_artifacts
from nested_doc_rag.cli import build_parser, step15_agentic_cli_overrides
from nested_doc_rag.io import read_json, read_jsonl


def test_agentic_trace_artifacts_are_written(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")]])
    answer = SequenceAnswer(
        [answer_json(status="answered", value="2路市电", state_kind="sufficient", source_chunk_ids=["initial"], confidence=0.95)]
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer)

    runner.run([make_item()])

    assert read_jsonl(tmp_path / "agentic_mas_trace.jsonl")
    assert read_jsonl(tmp_path / "agentic_round_states.jsonl") == []
    summary = read_json(tmp_path / "agentic_summary.json")
    assert summary["fields"][0]["initial_status"] == "answered"
    assert summary["fields"][0]["final_status"] == "answered"
    assert summary["fields"][0]["stopped_reason"] == "base_writeback_allowed"
    assert summary["fields"][0]["fourmode_attempted"] is False
    assert len(answer.calls) == 1
    assert validate_step15_artifacts(tmp_path)["valid"] is True


def test_equivalent_mas_does_not_write_agentic_artifacts(tmp_path: Path) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")]])
    answer = SequenceAnswer(
        [answer_json(status="answered", value="2路市电", state_kind="sufficient", source_chunk_ids=["initial"], confidence=0.95)]
    )
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer, mode="equivalent_mas")

    runner.run([make_item()])

    assert (tmp_path / "mas_trace.jsonl").exists()
    assert not (tmp_path / "agentic_mas_trace.jsonl").exists()
    assert not (tmp_path / "agentic_round_states.jsonl").exists()
    assert not (tmp_path / "agentic_summary.json").exists()


def test_agentic_cli_overrides_enable_mode_and_disable_workflow() -> None:
    args = build_parser().parse_args(
        [
            "run-step15-agent",
            "--out-dir",
            "artifacts/runs/test",
            "--agentic-mas",
            "--agentic-max-rounds",
            "4",
            "--disable-missing-info",
        ]
    )

    overrides = step15_agentic_cli_overrides(args)

    assert overrides["agentscope"] == {"enabled": True, "mode": "agentic_mas"}
    assert overrides["agentic_mas"]["enabled"] is True
    assert overrides["agentic_mas"]["max_rounds"] == 4
    assert overrides["agentic_mas"]["workflows"]["missing_info"] == {"enabled": False}


@pytest.mark.parametrize(
    ("disabled_workflows", "answer", "forbidden_actions"),
    [
        (
            {"missing_info": False},
            answer_json(status="partial_clue", state_kind="missing_info", missing_need="台数", failure_modes=["slot_missing"]),
            {"slot_targeted_retrieval"},
        ),
        (
            {"wrong_answer_risk": False},
            answer_json(
                status="answered",
                value="1路市电",
                state_kind="wrong_answer_risk",
                source_chunk_ids=["initial"],
                failure_modes=["weak_grounding"],
            ),
            {"contrastive_retrieval"},
        ),
        (
            {"not_found_recovery": False},
            answer_json(status="not_found", state_kind="not_found_recovery", failure_modes=["evidence_absence"]),
            {"alias_retrieval", "layer_expansion", "source_specific_retrieval"},
        ),
        (
            {"uncertainty_conflict": False},
            answer_json(
                status="conflict_unresolved",
                state_kind="uncertainty_conflict",
                failure_modes=["candidate_conflict"],
                candidates=["1路市电", "2路市电"],
            ),
            {"disambiguation_retrieval", "contrastive_retrieval"},
        ),
    ],
)
def test_disabled_workflow_action_types_do_not_appear_in_trace(
    tmp_path: Path,
    disabled_workflows: dict[str, bool],
    answer: dict,
    forbidden_actions: set[str],
) -> None:
    retrieval = SequenceRetrieval([[make_hit("initial")]])
    answer_caller = SequenceAnswer([answer])
    base_config = make_agentic_runner(tmp_path / "probe", retrieval=SequenceRetrieval([[make_hit("probe")]]), answer=SequenceAnswer([answer])).config
    config = agentic_config(base_config.agentic_mas, workflows_config=workflows(**disabled_workflows))
    runner = make_agentic_runner(tmp_path, retrieval=retrieval, answer=answer_caller, mas_config=config)

    runner.run([make_item()])

    seen = set()
    for event in read_jsonl(tmp_path / "agentic_mas_trace.jsonl"):
        payload = event.get("payload") or {}
        for action in payload.get("actions") or []:
            seen.add(action.get("action_type"))
        action_payload = payload.get("action") or {}
        if action_payload.get("action_type"):
            seen.add(action_payload["action_type"])
    assert forbidden_actions.isdisjoint(seen)
