from __future__ import annotations

from time import perf_counter
from typing import Any, Protocol

from nested_doc_rag.evaluation.step15_engine import add_room_context, build_qdrant_answer_messages
from nested_doc_rag.gongkan_eval import build_masked_query

from .schemas import AnswerArbitrationOutput, EvidenceRetrievalOutput, OverlayControlOutput, QueryPlanOutput


class Step15RunnerProtocol(Protocol):
    target_namespace: str
    room_context: str | None
    prompt_version: str
    retrieval_mode: str

    def retrieve(self, query_text: str) -> Any: ...

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


class AnswerArbitrationRole:
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
