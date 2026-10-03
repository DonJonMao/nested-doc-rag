from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from nested_doc_rag.evaluation.step15_engine import add_room_context, build_qdrant_answer_messages
from nested_doc_rag.gongkan_eval import build_masked_query

from .schemas import (
    ActionType,
    AgenticMASState,
    AnswerArbitrationOutput,
    EvidenceAction,
    EvidenceRetrievalOutput,
    OverlayControlOutput,
    QueryPlanOutput,
    parse_evidence_diagnosis,
)


class Step15RunnerProtocol(Protocol):
    target_namespace: str
    room_context: str | None
    prompt_version: str
    retrieval_mode: str

    def retrieve(
        self, query_text: str, *, layer_names: list[str] | None = None, source_types: list[str] | None = None
    ) -> Any: ...

    def call_answer(self, **kwargs: Any) -> dict[str, Any]: ...


class QueryPlannerRole:
    name = "query_planner"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, item: dict[str, Any]) -> QueryPlanOutput:
        base_query = build_masked_query(item, self.runner.target_namespace)
        query_text = add_room_context(base_query, self.runner.room_context)
        return QueryPlanOutput(base_query=base_query, query_text=query_text)


class EvidenceRetrievalRole:
    name = "evidence_retrieval"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(
        self, query_text: str, *, layer_names: list[str] | None = None, source_types: list[str] | None = None
    ) -> EvidenceRetrievalOutput:
        started = perf_counter_ms()
        if layer_names is None and source_types is None:
            retrieval_result = self.runner.retrieve(query_text)
        else:
            retrieval_result = self.runner.retrieve(query_text, layer_names=layer_names, source_types=source_types)
        retrieval_latency_ms = round(perf_counter_ms() - started, 3)
        top_hits = retrieval_result.reranked_hits
        vector_hits = retrieval_result.vector_hits
        return EvidenceRetrievalOutput(
            retrieval_result=retrieval_result,
            top_hits=top_hits,
            vector_hits=vector_hits,
            retrieval_latency_ms=retrieval_latency_ms,
        )

    def run_action(self, action: EvidenceAction) -> EvidenceRetrievalOutput:
        return self.run(
            action.query_text,
            layer_names=_action_constraint_values(action.target_layer),
            source_types=_action_constraint_values(action.source_type_preference),
        )


def _action_constraint_values(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))


def _configured_constraint(runner: Step15RunnerProtocol, preferred: list[str], *, layers: bool) -> str:
    plan = getattr(runner, "layered_plan", None)
    if plan is None:
        return ",".join(preferred)
    if not layers and any(not spec["source_types"] for spec in plan):
        return ",".join(preferred)
    configured = (
        {str(spec["layer_name"]) for spec in plan}
        if layers
        else {str(source) for spec in plan for source in spec["source_types"]}
    )
    return ",".join(value for value in preferred if value in configured)


class QueryReplannerRole:
    name = "query_replanner"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, state: AgenticMASState, *, workflow: str) -> list[EvidenceAction]:
        if workflow == "missing_info":
            return self.run_missing_info(state)
        if workflow == "not_found_recovery":
            return self.run_not_found_recovery(state)
        if workflow == "uncertainty_conflict":
            return self.run_disambiguation(state)
        return []

    def run_missing_info(self, state: AgenticMASState) -> list[EvidenceAction]:
        diagnosis = state.diagnosis
        target_slot = _first_missing_slot(state)
        query = _query_with_focus(
            state,
            f"补齐缺失槽位：{target_slot}；只检索同一机房、同一填报项、同一指标粒度的直接证据。",
            self.runner.room_context,
        )
        return [
            EvidenceAction(
                action_type=ActionType.SLOT_TARGETED_RETRIEVAL,
                query_text=query,
                target_slot=target_slot,
                purpose=(diagnosis.missing_information_need if diagnosis else None) or "recover missing direct evidence",
                semantic_invariant=_semantic_invariant(state),
                expected_gain_type="missing_slot_direct_evidence",
            )
        ]

    def run_not_found_recovery(self, state: AgenticMASState) -> list[EvidenceAction]:
        invariant = _semantic_invariant(state)
        return [
            EvidenceAction(
                action_type=ActionType.ALIAS_RETRIEVAL,
                query_text=_query_with_focus(state, "使用字段别名、类别路径、指标同义表达重新检索，但保持原实体和原属性不变。", self.runner.room_context),
                purpose="recover evidence through aliases",
                semantic_invariant=invariant,
                expected_gain_type="alias_match",
            ),
            EvidenceAction(
                action_type=ActionType.LAYER_EXPANSION,
                query_text=_query_with_focus(state, "扩大到目标知识库下钻表格、原文段落和低优先级层；保持同一字段问题不变。", self.runner.room_context),
                target_layer=_configured_constraint(
                    self.runner,
                    ["target_table_detail", "target_text_detail", "target_structured_detail", "target_raw_detail", "target_uploaded", "global_detail", "global_uploaded"],
                    layers=True,
                ),
                purpose="recover evidence from lower layers",
                semantic_invariant=invariant,
                expected_gain_type="layer_expansion_match",
            ),
            EvidenceAction(
                action_type=ActionType.SOURCE_SPECIFIC_RETRIEVAL,
                query_text=_query_with_focus(
                    state,
                    "优先检索主能力表、下钻明细和上传资料中与该字段直接对应的来源。",
                    self.runner.room_context,
                ),
                source_type_preference=_configured_constraint(
                    self.runner,
                    [
                        "main_excel_capability", "embedded_word_table", "embedded_raw_segment",
                        "uploaded_excel_row", "uploaded_docx_paragraph", "uploaded_docx_table_row", "uploaded_text_chunk",
                    ],
                    layers=False,
                ),
                purpose="recover evidence from source-specific search",
                semantic_invariant=invariant,
                expected_gain_type="source_specific_match",
            ),
        ]

    def run_disambiguation(self, state: AgenticMASState) -> list[EvidenceAction]:
        candidates = _candidate_values(state)
        focus = "区分候选答案冲突；查找同一实体、同一属性、同一粒度下可裁决候选值的直接证据。"
        if candidates:
            focus += f" 候选值：{', '.join(candidates)}。"
        return [
            EvidenceAction(
                action_type=ActionType.DISAMBIGUATION_RETRIEVAL,
                query_text=_query_with_focus(state, focus, self.runner.room_context),
                purpose="disambiguate conflicting candidates",
                semantic_invariant=_semantic_invariant(state),
                expected_gain_type="candidate_disambiguation",
            )
        ]


class SkepticRole:
    name = "skeptic"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, state: AgenticMASState, *, workflow: str) -> list[EvidenceAction]:
        if workflow == "wrong_answer_risk":
            return self.run_wrong_answer_risk(state)
        if workflow == "uncertainty_conflict":
            return self.run_candidate_challenges(state)
        return []

    def run_wrong_answer_risk(self, state: AgenticMASState) -> list[EvidenceAction]:
        answer_value = _current_answer_value(state)
        focus = (
            f"主动查找能反驳或修正当前答案“{answer_value}”的证据；"
            "必须保持同一机房、同一字段、同一指标粒度，不寻找泛化支持材料。"
        )
        return [
            EvidenceAction(
                action_type=ActionType.CONTRASTIVE_RETRIEVAL,
                query_text=_query_with_focus(state, focus, self.runner.room_context),
                purpose="challenge risky answered belief",
                semantic_invariant=_semantic_invariant(state),
                expected_gain_type="refuting_or_correcting_evidence",
            )
        ]

    def run_candidate_challenges(self, state: AgenticMASState) -> list[EvidenceAction]:
        candidates = _candidate_values(state) or [_current_answer_value(state)]
        actions: list[EvidenceAction] = []
        for candidate in candidates:
            focus = f"挑战候选答案“{candidate}”；检索同一实体、同一字段、同一粒度下支持其他候选或反驳该候选的证据。"
            actions.append(
                EvidenceAction(
                    action_type=ActionType.CONTRASTIVE_RETRIEVAL,
                    query_text=_query_with_focus(state, focus, self.runner.room_context),
                    purpose=f"challenge candidate: {candidate}",
                    semantic_invariant=_semantic_invariant(state),
                    expected_gain_type="candidate_challenge",
                )
            )
        return actions


class AnswerArbitrationRole:
    name = "answer_arbitration"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(
        self,
        item: dict[str, Any],
        query_text: str,
        top_hits: list[dict[str, Any]],
        *,
        slot_schema: dict[str, Any] | None = None,
    ) -> AnswerArbitrationOutput:
        from nested_doc_rag.agent.step15_runner import convert_step15_generated_to_prediction

        started = perf_counter_ms()
        messages = build_qdrant_answer_messages(
            item,
            query_text,
            top_hits,
            room_context=None,
            prompt_version=getattr(self.runner, "agentic_prompt_version", self.runner.prompt_version),
            slot_schema=slot_schema,
        )
        generated = self.runner.call_answer(messages=messages, item=item, query_text=query_text, hits=top_hits)
        generation_latency_ms = round(perf_counter_ms() - started, 3)
        prediction = convert_step15_generated_to_prediction(item, generated, top_hits, retrieval_mode=self.runner.retrieval_mode)
        diagnosis = parse_evidence_diagnosis(generated, top_hits)
        return AnswerArbitrationOutput(
            generated=generated,
            prediction=prediction,
            generation_latency_ms=generation_latency_ms,
            diagnosis=diagnosis,
        )


class OverlayControlRole:
    name = "overlay_control"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, item: dict[str, Any], generated: dict[str, Any], prediction: Any, top_hits: list[dict[str, Any]]) -> OverlayControlOutput:
        from nested_doc_rag.agent.step15_runner import (
            build_agent_overlay_for_step15_prediction,
            critic_check_step15_answer,
            make_step15_review_item,
        )

        critic_flags = critic_check_step15_answer(item, generated, top_hits)
        overlay = build_agent_overlay_for_step15_prediction(prediction, top_hits, critic_flags)
        review_item = make_step15_review_item(item, prediction, overlay, top_hits)
        return OverlayControlOutput(critic_flags=critic_flags, overlay=overlay, review_item=review_item)


def perf_counter_ms() -> float:
    return perf_counter() * 1000


def _semantic_invariant(state: AgenticMASState) -> dict[str, Any]:
    item = state.item
    return {
        "form_item_id": item.get("form_item_id"),
        "row_index": item.get("row_index"),
        "target_cell": item.get("target_cell"),
        "question_text": item.get("question_text"),
        "instruction_text": item.get("instruction_text"),
        "category_path": item.get("category_path") or [],
        "base_query": state.base_query,
    }


def _query_with_focus(state: AgenticMASState, focus: str, room_context: str | None) -> str:
    return add_room_context(f"{state.base_query}。{focus}", room_context)


def _first_missing_slot(state: AgenticMASState) -> str:
    diagnosis = state.diagnosis
    if diagnosis and diagnosis.missing_information_need:
        return diagnosis.missing_information_need
    generated = state.generated or {}
    for item in generated.get("missing_fields") or []:
        if item:
            return str(item)
    return str(state.item.get("question_text") or state.item.get("instruction_text") or "field")


def _candidate_values(state: AgenticMASState) -> list[str]:
    diagnosis = state.diagnosis
    if diagnosis is None:
        return []
    return [candidate.value for candidate in diagnosis.candidate_answers if candidate.value]


def _current_answer_value(state: AgenticMASState) -> str:
    if state.prediction is not None and state.prediction.answer_value:
        return str(state.prediction.answer_value)
    generated = state.generated or {}
    return str(generated.get("answer_value") or "未找到")
