from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from nested_doc_rag.evaluation.step15_engine import add_room_context, build_qdrant_answer_messages
from nested_doc_rag.gongkan_eval import build_masked_query

from .schemas import (
    AnswerArbitrationOutput,
    EvidenceRetrievalOutput,
    EvidenceScoutReport,
    OverlayControlOutput,
    QueryPlan,
    SemanticRiskReport,
)


class Step15RunnerProtocol(Protocol):
    target_namespace: str
    room_context: str | None
    prompt_version: str
    retrieval_mode: str

    def retrieve(self, query_text: str) -> Any: ...

    def call_answer(self, **kwargs: Any) -> dict[str, Any]: ...


class QueryPlannerAgent:
    name = "query_planner"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, item: dict[str, Any]) -> QueryPlan:
        base_query = build_masked_query(item, self.runner.target_namespace)
        query_text = add_room_context(base_query, self.runner.room_context)
        question = str(item.get("question_text") or "")
        instruction = str(item.get("instruction_text") or "")
        evidence_slots = [slot for slot in [question, instruction] if slot]
        fallback_queries = [
            add_room_context(" ".join(part for part in [question, instruction] if part), self.runner.room_context),
            base_query,
        ]
        fallback_queries = [query for query in dict.fromkeys(query for query in fallback_queries if query and query != query_text)]
        return QueryPlan(
            base_query=base_query,
            query_text=query_text,
            primary_query=query_text,
            fallback_queries=fallback_queries,
            evidence_slots=evidence_slots,
            answer_constraints=["use_retrieved_sources_only", "preserve_step15_answer_schema"],
            preferred_layers=["target_main_fact", "target_structured_detail", "target_raw_detail"],
            source_constraints=["target_before_global", "no_global_intro_override_without_target_evidence"],
        )


class EvidenceRetrievalAgent:
    name = "evidence_retrieval"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, query_text: str) -> EvidenceRetrievalOutput:
        started = perf_counter_ms()
        retrieval_result = self.runner.retrieve(query_text)
        retrieval_latency_ms = round(perf_counter_ms() - started, 3)
        top_hits = retrieval_result.reranked_hits
        vector_hits = retrieval_result.vector_hits
        return EvidenceRetrievalOutput(
            retrieval_result=retrieval_result,
            top_hits=top_hits,
            vector_hits=vector_hits,
            retrieval_latency_ms=retrieval_latency_ms,
        )


class AnswerArbiterAgent:
    name = "answer_arbitration"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, item: dict[str, Any], query_text: str, top_hits: list[dict[str, Any]]) -> AnswerArbitrationOutput:
        from nested_doc_rag.agent.step15_runner import convert_step15_generated_to_prediction

        started = perf_counter_ms()
        messages = build_qdrant_answer_messages(
            item,
            query_text,
            top_hits,
            room_context=self.runner.room_context,
            prompt_version=self.runner.prompt_version,
        )
        generated = self.runner.call_answer(messages=messages, item=item, query_text=query_text, hits=top_hits)
        generation_latency_ms = round(perf_counter_ms() - started, 3)
        prediction = convert_step15_generated_to_prediction(item, generated, top_hits, retrieval_mode=self.runner.retrieval_mode)
        return AnswerArbitrationOutput(generated=generated, prediction=prediction, generation_latency_ms=generation_latency_ms)


class EvidenceScoutAgent:
    name = "evidence_scout"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, item: dict[str, Any], query_plan: QueryPlan, baseline_hits: list[dict[str, Any]]) -> EvidenceScoutReport:
        field_id = str(item.get("form_item_id") or f"row_{item.get('row_index')}")
        missing_slots: list[str] = []
        text = " ".join(str(hit.get("raw_text") or hit.get("text_for_embedding") or "") for hit in baseline_hits).lower()
        for slot in query_plan.evidence_slots:
            tokens = [token for token in str(slot).lower().replace("，", " ").replace("。", " ").split() if len(token) >= 2]
            if tokens and not any(token in text for token in tokens):
                missing_slots.append(str(slot))
        evidence_sufficient = bool(baseline_hits) and not missing_slots
        supplemental_queries = list(query_plan.fallback_queries)
        if missing_slots:
            supplemental_queries.extend(add_room_context(slot, self.runner.room_context) for slot in missing_slots)
        return EvidenceScoutReport(
            field_id=field_id,
            evidence_sufficient=evidence_sufficient,
            missing_slots=list(dict.fromkeys(missing_slots)),
            conflict_suspected=False,
            supplemental_queries=list(dict.fromkeys(query for query in supplemental_queries if query)),
            rationale="baseline evidence satisfies slots" if evidence_sufficient else "baseline evidence missing semantic slots",
        )


class RiskCriticAgent:
    name = "risk_critic"

    def __init__(self, runner: Step15RunnerProtocol) -> None:
        self.runner = runner

    def run(self, item: dict[str, Any], generated: dict[str, Any], top_hits: list[dict[str, Any]]) -> SemanticRiskReport:
        field_id = str(item.get("form_item_id") or f"row_{item.get('row_index')}")
        del generated, top_hits
        return SemanticRiskReport(field_id=field_id, semantic_risk_level="low", risk_reasons=[], suggest_review=False)


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


QueryPlannerRole = QueryPlannerAgent
EvidenceRetrievalRole = EvidenceRetrievalAgent
AnswerArbitrationRole = AnswerArbiterAgent
