from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import agentic_config, make_item, workflows

from nested_doc_rag.agent.mas.controller import Step15MASController
from nested_doc_rag.agent.mas.schemas import AgenticMASState, EvidenceDiagnosis, EvidenceStateKind
from nested_doc_rag.config import load_app_config


@pytest.mark.parametrize(
    ("state_kind", "disabled_workflows"),
    [
        (EvidenceStateKind.MISSING_INFO, {"missing_info": False}),
        (EvidenceStateKind.WRONG_ANSWER_RISK, {"wrong_answer_risk": False}),
        (EvidenceStateKind.NOT_FOUND_RECOVERY, {"not_found_recovery": False}),
        (EvidenceStateKind.UNCERTAINTY_CONFLICT, {"uncertainty_conflict": False}),
    ],
)
def test_disabled_workflow_routes_to_mark_unresolved(tmp_path: Path, state_kind: EvidenceStateKind, disabled_workflows: dict[str, bool]) -> None:
    cfg = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")
    mas_cfg = agentic_config(cfg.agentic_mas, workflows_config=workflows(**disabled_workflows))
    controller = Step15MASController(FakeRunner(), mode="agentic_mas")
    state = AgenticMASState(
        item=make_item(),
        base_query="base",
        current_query="base",
        round_index=0,
        evidence=[],
        vector_hits=[],
        generated={"answer_status": "partial_clue"},
        prediction=None,
        diagnosis=EvidenceDiagnosis(
            state_kind=state_kind,
            failure_modes=[],
            sufficiency="insufficient",
            missing_information_need="slot",
            candidate_answers=[],
            risk_reason="risk",
            recommended_next_actions=[],
        ),
    )

    actions = controller.select_actions(state, mas_cfg)

    assert [action.action_type.value for action in actions] == ["mark_unresolved"]


def test_enabled_uncertainty_conflict_selects_replanner_and_skeptic_actions(tmp_path: Path) -> None:
    cfg = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")
    mas_cfg = replace(agentic_config(cfg.agentic_mas), max_actions_per_round=3)
    controller = Step15MASController(FakeRunner(), mode="agentic_mas")
    state = AgenticMASState(
        item=make_item(),
        base_query="base",
        current_query="base",
        round_index=0,
        evidence=[],
        vector_hits=[],
        generated={"answer_status": "conflict_unresolved"},
        prediction=None,
        diagnosis=EvidenceDiagnosis(
            state_kind=EvidenceStateKind.UNCERTAINTY_CONFLICT,
            failure_modes=[],
            sufficiency="contradictory",
            missing_information_need=None,
            candidate_answers=[],
            risk_reason="conflict",
            recommended_next_actions=[],
        ),
    )

    action_types = [action.action_type.value for action in controller.select_actions(state, mas_cfg)]

    assert "disambiguation_retrieval" in action_types
    assert "contrastive_retrieval" in action_types


class FakeRunner:
    target_namespace = "xixian_4"
    room_context = None
    prompt_version = "agentic_v1"
    retrieval_mode = "layered"

    def retrieve(self, query_text: str) -> Any:
        raise AssertionError(query_text)

    def call_answer(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError(kwargs)
