from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult
from nested_doc_rag.schemas.eval import FieldPrediction


@dataclass(frozen=True)
class QueryPlanOutput:
    base_query: str
    query_text: str


@dataclass(frozen=True)
class EvidenceRetrievalOutput:
    retrieval_result: Step15RetrievalResult
    top_hits: list[dict[str, Any]]
    vector_hits: list[dict[str, Any]]
    retrieval_latency_ms: float


@dataclass(frozen=True)
class AnswerArbitrationOutput:
    generated: dict[str, Any]
    prediction: FieldPrediction
    generation_latency_ms: float


@dataclass(frozen=True)
class OverlayControlOutput:
    critic_flags: list[str]
    overlay: Any
    review_item: dict[str, Any] | None
