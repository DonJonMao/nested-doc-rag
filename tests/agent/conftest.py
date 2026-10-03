from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

from nested_doc_rag.agent.step15_runner import Step15AgentRunner
from nested_doc_rag.config import (
    AgenticMASConfig,
    AgenticMASWorkflowsConfig,
    AgenticMASWorkflowSwitchConfig,
    AgentScopeConfig,
    load_app_config,
)
from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult


def make_item(row: int = 4, *, question_text: str = "市电进线情况") -> dict[str, Any]:
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


def make_hit(chunk_id: str, text: str | None = None) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "namespace": "xixian_4",
        "source_type": "main_excel_capability",
        "corpus_layer": "fact",
        "retrieval_layer": "target_main_fact",
        "layer_priority": 1,
        "rerank_score": 0.9,
        "file_name": "main.xlsx",
        "anchor": chunk_id,
        "raw_text": text or f"{chunk_id} evidence",
        "text_for_embedding": text or f"{chunk_id} evidence",
        "proof_attachment_ids": [f"att_{chunk_id}"],
    }


class SequenceRetrieval:
    def __init__(self, responses: list[list[dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, query: str) -> Step15RetrievalResult:
        self.calls.append(query)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        hits = copy.deepcopy(self.responses[index])
        return Step15RetrievalResult(reranked_hits=hits, vector_hits=copy.deepcopy(hits), retrieval_mode="layered")


class SequenceAnswer:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return copy.deepcopy(self.responses[index])


def answer_json(
    *,
    status: str,
    value: str = "未找到",
    state_kind: str,
    failure_modes: list[str] | None = None,
    source_chunk_ids: list[str] | None = None,
    confidence: float = 0.5,
    missing_need: str | None = None,
    candidates: list[str] | None = None,
    risk_reason: str | None = None,
) -> dict[str, Any]:
    source_chunk_ids = source_chunk_ids or []
    return {
        "answer_value": value,
        "answer_status": status,
        "confidence": confidence,
        "source_chunk_ids": source_chunk_ids,
        "evidence_attachment_ids": [f"att_{chunk_id}" for chunk_id in source_chunk_ids],
        "reference_source_documents": []
        if status == "answered"
        else [{"chunk_id": "initial", "file_name": "main.xlsx", "anchor": "row", "reason": "related clue"}],
        "agent_resolution": {"used": True, "action": "select_source", "reason": "fake"},
        "missing_fields": [missing_need] if missing_need else [],
        "evidence_diagnosis": {
            "state_kind": state_kind,
            "failure_modes": failure_modes or [],
            "sufficiency": "sufficient" if state_kind == "sufficient" else "insufficient",
            "missing_information_need": missing_need,
            "candidate_answers": [
                {
                    "value": candidate,
                    "supporting_chunk_ids": [],
                    "refuting_chunk_ids": [],
                    "scope": None,
                    "reason": "fake candidate",
                }
                for candidate in candidates or []
            ],
            "risk_reason": risk_reason,
            "recommended_next_actions": [],
        },
    }


def workflows(
    *,
    missing_info: bool = True,
    wrong_answer_risk: bool = True,
    not_found_recovery: bool = True,
    uncertainty_conflict: bool = True,
) -> AgenticMASWorkflowsConfig:
    return AgenticMASWorkflowsConfig(
        missing_info=AgenticMASWorkflowSwitchConfig(enabled=missing_info),
        wrong_answer_risk=AgenticMASWorkflowSwitchConfig(enabled=wrong_answer_risk),
        not_found_recovery=AgenticMASWorkflowSwitchConfig(enabled=not_found_recovery),
        uncertainty_conflict=AgenticMASWorkflowSwitchConfig(enabled=uncertainty_conflict),
    )


def agentic_config(
    base: AgenticMASConfig,
    *,
    max_rounds: int = 2,
    max_actions_per_round: int = 2,
    workflows_config: AgenticMASWorkflowsConfig | None = None,
) -> AgenticMASConfig:
    return replace(
        base,
        enabled=True,
        max_rounds=max_rounds,
        max_actions_per_round=max_actions_per_round,
        workflows=workflows_config or base.workflows,
    )


def make_agentic_runner(
    out_dir: Path,
    *,
    retrieval: SequenceRetrieval,
    answer: SequenceAnswer,
    mas_config: AgenticMASConfig | None = None,
    mode: str = "agentic_mas",
) -> Step15AgentRunner:
    config = load_app_config(
        project_root=out_dir,
        default_config=out_dir / "missing.yaml",
        cli_overrides={"retrieval": {"sufficiency_enabled": False}},
    )
    config = replace(
        config,
        agentscope=AgentScopeConfig(enabled=mode != "off", mode=mode),
        agentic_mas=mas_config or agentic_config(config.agentic_mas),
        grounding=replace(
            config.grounding,
            slot_decomposition_enabled=False,
            pre_writeback_consistency_enabled=False,
        ),
    )
    return Step15AgentRunner(
        config=config,
        target_namespace="xixian_4",
        global_namespace="global",
        room_context="西咸4号楼 301机房",
        out_dir=out_dir,
        retrieval_plan="layered",
        retrieval_fn=retrieval,
        answer_caller=answer,
        writeback_enabled=False,
        chat_retry_backoff_seconds=0,
        grounding_enabled=False,
        field_binding_enabled=False,
    )
