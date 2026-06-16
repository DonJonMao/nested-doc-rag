from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nested_doc_rag.evaluation.step15_engine import Step15RetrievalResult
from nested_doc_rag.schemas.eval import FieldPrediction


@dataclass(frozen=True)
class QueryPlan:
    base_query: str
    query_text: str
    primary_query: str
    fallback_queries: list[str]
    evidence_slots: list[str]
    answer_constraints: list[str]
    preferred_layers: list[str]
    source_constraints: list[str]


QueryPlanOutput = QueryPlan


@dataclass(frozen=True)
class EvidenceRequirement:
    slot: str
    required: bool = True


@dataclass(frozen=True)
class EvidenceScoutReport:
    field_id: str
    evidence_sufficient: bool
    missing_slots: list[str]
    conflict_suspected: bool
    supplemental_queries: list[str]
    rationale: str


@dataclass(frozen=True)
class SupplementalRetrievalPlan:
    field_id: str
    enabled: bool
    reason: str
    queries: list[str]
    rounds: int


@dataclass(frozen=True)
class SemanticRiskReport:
    field_id: str
    semantic_risk_level: str
    risk_reasons: list[str]
    suggest_review: bool


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
