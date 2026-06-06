from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nested_doc_rag.schemas.agent import AgentAction, AgentTraceEvent
from nested_doc_rag.schemas.eval import FieldPrediction


@dataclass
class QueryPlan:
    field_id: str
    question_text: str
    primary_query: str
    fallback_queries: list[str]
    target_namespace: str
    fallback_namespaces: list[str]
    preferred_source_types: list[str]
    required_evidence: bool
    intent: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "question_text": self.question_text,
            "primary_query": self.primary_query,
            "fallback_queries": self.fallback_queries,
            "target_namespace": self.target_namespace,
            "fallback_namespaces": self.fallback_namespaces,
            "preferred_source_types": self.preferred_source_types,
            "required_evidence": self.required_evidence,
            "intent": self.intent,
            "reason": self.reason,
        }


@dataclass
class EvidenceBundle:
    field_id: str
    selected_chunks: list[dict[str, Any]]
    reference_chunks: list[dict[str, Any]]
    ignored_chunks: list[dict[str, Any]]
    decision: str
    reason: str
    conflict_detected: bool = False
    answer_status_hint: str = "answered"

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "selected_chunks": self.selected_chunks,
            "reference_chunks": self.reference_chunks,
            "ignored_chunks": self.ignored_chunks,
            "decision": self.decision,
            "reason": self.reason,
            "conflict_detected": self.conflict_detected,
            "answer_status_hint": self.answer_status_hint,
        }


@dataclass
class ValidationResult:
    passed: bool
    violations: list[str]
    needs_human_review: bool = False
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "needs_human_review": self.needs_human_review,
            "confidence": self.confidence,
        }


@dataclass
class RepairDecision:
    should_repair: bool
    repair_type: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_repair": self.should_repair,
            "repair_type": self.repair_type,
            "reason": self.reason,
        }


@dataclass
class FieldState:
    field_id: str
    row_index: int
    target_cell: str | None
    question_text: str
    field_type: str
    required: bool
    must_have_evidence: bool
    constraints: Any
    query_plan: QueryPlan | None = None
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    evidence_bundle: EvidenceBundle | None = None
    draft_prediction: FieldPrediction | None = None
    validation_result: ValidationResult | None = None
    repair_attempts: list[dict[str, Any]] = field(default_factory=list)
    final_prediction: FieldPrediction | None = None
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_id": self.field_id,
            "row_index": self.row_index,
            "target_cell": self.target_cell,
            "question_text": self.question_text,
            "field_type": self.field_type,
            "required": self.required,
            "must_have_evidence": self.must_have_evidence,
            "constraints": self.constraints.to_dict() if hasattr(self.constraints, "to_dict") else self.constraints,
            "query_plan": self.query_plan.to_dict() if self.query_plan else None,
            "retrieved_chunks": self.retrieved_chunks,
            "evidence_bundle": self.evidence_bundle.to_dict() if self.evidence_bundle else None,
            "draft_prediction": self.draft_prediction.to_dict() if self.draft_prediction else None,
            "validation_result": self.validation_result.to_dict() if self.validation_result else None,
            "repair_attempts": self.repair_attempts,
            "final_prediction": self.final_prediction.to_dict() if self.final_prediction else None,
            "status": self.status,
        }


@dataclass
class RunState:
    run_id: str
    target_namespace: str
    out_dir: Path
    fields_total: int
    fields_completed: int = 0
    fields_human_review: int = 0
    fields_failed: int = 0
    started_at: str = ""
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target_namespace": self.target_namespace,
            "out_dir": str(self.out_dir),
            "fields_total": self.fields_total,
            "fields_completed": self.fields_completed,
            "fields_human_review": self.fields_human_review,
            "fields_failed": self.fields_failed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


__all__ = [
    "AgentAction",
    "AgentTraceEvent",
    "EvidenceBundle",
    "FieldState",
    "QueryPlan",
    "RepairDecision",
    "RunState",
    "ValidationResult",
]
