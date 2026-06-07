from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nested_doc_rag.io import md, read_jsonl, write_json, write_jsonl
from nested_doc_rag.schemas.agent import AgentTraceEvent

SENSITIVE_KEYS = {"api_key", "authorization", "token", "password", "secret"}
TEXT_KEYS = {"text", "raw_text", "content", "prompt"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def make_trace_event(event_id: str, event_type: str, message: str) -> AgentTraceEvent:
    return AgentTraceEvent(event_id=event_id, event_type=event_type, message=message)


@dataclass
class TraceEvent:
    run_id: str
    field_id: str | None
    step: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "field_id": self.field_id,
            "step": self.step,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


class TraceRecorder:
    def __init__(self, run_id: str, metadata: dict[str, Any] | None = None):
        self.run_id = run_id
        self.metadata = metadata or {}
        self.events: list[TraceEvent] = []

    def set_metadata(self, metadata: dict[str, Any]) -> None:
        self.metadata.update(metadata)

    def record(self, field_id: str | None, step: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(
            TraceEvent(
                run_id=self.run_id,
                field_id=field_id,
                step=step,
                timestamp=now_iso(),
                payload=sanitize_payload(payload or {}),
            )
        )

    def write_jsonl(self, path: Path) -> None:
        write_jsonl(path, [event.to_dict() for event in self.events])

    def load_jsonl(self, path: Path) -> None:
        if not path.exists():
            return
        self.events = []
        for record in read_trace_jsonl(path):
            self.events.append(
                TraceEvent(
                    run_id=str(record.get("run_id") or self.run_id),
                    field_id=record.get("field_id"),
                    step=str(record.get("step") or ""),
                    timestamp=str(record.get("timestamp") or now_iso()),
                    payload=dict(record.get("payload") or {}),
                )
            )

    def write_summary(self, path: Path) -> None:
        write_json(path, self.summary())

    def write_markdown(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_trace_markdown(self.events, self.summary()), encoding="utf-8")

    def summary(self) -> dict[str, Any]:
        field_ids = {event.field_id for event in self.events if event.field_id}
        completed = [event for event in self.events if event.step == "field_completed"]
        status_counts = {"answered": 0, "partial_clue": 0, "not_found": 0, "conflict_unresolved": 0}
        for event in completed:
            status = (event.payload.get("final_prediction") or {}).get("answer_status")
            if status in status_counts:
                status_counts[status] += 1
        qdrant_hit_total = 0
        skip_reasons = {"no_evidence": 0, "reference_only": 0, "conflict": 0}
        direct_evidence_count = 0
        reference_only_count = 0
        for event in self.events:
            if event.step == "evidence_retrieved":
                metadata = event.payload.get("retrieval_metadata") or {}
                qdrant_hit_total += int(metadata.get("qdrant_hit_count") or 0)
            if event.step == "evidence_selected":
                bundle = event.payload.get("evidence_bundle") or {}
                if bundle.get("decision") == "use_direct_evidence":
                    direct_evidence_count += 1
                elif bundle.get("decision") == "clue_only":
                    reference_only_count += 1
            if event.step == "answer_skipped":
                reason = str(event.payload.get("generation_skip_reason") or event.payload.get("reason") or "")
                if reason in skip_reasons:
                    skip_reasons[reason] += 1
        return {
            **self.metadata,
            "run_id": self.run_id,
            "total_events": len(self.events),
            "total_fields": len(field_ids),
            "answered_count": status_counts["answered"],
            "partial_clue_count": status_counts["partial_clue"],
            "not_found_count": status_counts["not_found"],
            "conflict_unresolved_count": status_counts["conflict_unresolved"],
            "human_review_count": sum(1 for event in self.events if event.step == "human_review_required"),
            "repaired_count": sum(1 for event in self.events if event.step == "repaired"),
            "generation_called_count": sum(1 for event in self.events if event.step == "answer_generated" and event.payload.get("generation_called")),
            "generation_skipped_count": sum(1 for event in self.events if event.step == "answer_skipped" or event.payload.get("generation_called") is False),
            "skipped_no_evidence_count": skip_reasons["no_evidence"],
            "skipped_reference_only_count": skip_reasons["reference_only"],
            "skipped_conflict_count": skip_reasons["conflict"],
            "direct_evidence_count": direct_evidence_count,
            "reference_only_count": reference_only_count,
            "resumed_count": sum(1 for event in self.events if event.step == "resume_started"),
            "skipped_completed_count": sum(int(event.payload.get("skipped_completed_count") or 0) for event in self.events if event.step == "resume_started"),
            "qdrant_hit_total": qdrant_hit_total,
        }


def sanitize_payload(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in SENSITIVE_KEYS or any(secret in lowered for secret in SENSITIVE_KEYS):
                output[key_text] = "[redacted]"
            elif lowered in TEXT_KEYS:
                output[key_text] = preview_text(item)
            else:
                output[key_text] = sanitize_payload(item)
        return output
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def preview_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def render_trace_markdown(events: list[TraceEvent], summary: dict[str, Any]) -> str:
    lines = [
        "# Field Filling Agent Trace",
        "",
        "## Summary",
        "",
        f"- run_id: `{summary['run_id']}`",
        f"- total_fields: {summary['total_fields']}",
        f"- total_events: {summary['total_events']}",
        f"- answered: {summary['answered_count']}",
        f"- partial_clue: {summary['partial_clue_count']}",
        f"- not_found: {summary['not_found_count']}",
        f"- conflict_unresolved: {summary['conflict_unresolved_count']}",
        f"- human_review: {summary['human_review_count']}",
        f"- repaired: {summary['repaired_count']}",
        f"- generation_called: {summary.get('generation_called_count', 0)}",
        f"- generation_skipped: {summary.get('generation_skipped_count', 0)}",
        f"- skipped_no_evidence: {summary.get('skipped_no_evidence_count', 0)}",
        f"- skipped_reference_only: {summary.get('skipped_reference_only_count', 0)}",
        f"- skipped_conflict: {summary.get('skipped_conflict_count', 0)}",
        f"- direct_evidence: {summary.get('direct_evidence_count', 0)}",
        f"- reference_only: {summary.get('reference_only_count', 0)}",
        f"- qdrant_hit_total: {summary.get('qdrant_hit_total', 0)}",
        "",
        "## Fields",
        "",
    ]
    for field_id in sorted({event.field_id for event in events if event.field_id}):
        lines.append(f"### {field_id}")
        lines.append("")
        for event in [item for item in events if item.field_id == field_id]:
            lines.append(f"- `{event.step}` at `{event.timestamp}`")
            detail = markdown_event_detail(event)
            if detail:
                lines.append(f"  {detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def markdown_event_detail(event: TraceEvent) -> str:
    payload = event.payload
    if event.step == "query_planned":
        plan = payload.get("query_plan") or {}
        return f"query: `{md(plan.get('primary_query'), 120)}`"
    if event.step == "evidence_selected":
        bundle = payload.get("evidence_bundle") or {}
        selected = chunk_labels(bundle.get("selected_chunks") or [])
        return f"decision: `{bundle.get('decision')}`, selected: {selected or 'none'}"
    if event.step in {"answer_generated", "answer_skipped", "field_completed"}:
        prediction = payload.get("prediction") or payload.get("final_prediction") or {}
        return f"answer_status: `{prediction.get('answer_status')}`, value: `{md(prediction.get('answer_value'), 80)}`"
    if event.step == "validated":
        validation = payload.get("validation_result") or {}
        return f"passed: `{validation.get('passed')}`, violations: `{validation.get('violations')}`"
    if event.step == "repaired":
        return f"repair: `{md(payload.get('repair_log'), 120)}`"
    if event.step == "human_review_required":
        return f"reason: `{md(payload.get('reason'), 120)}`"
    return ""


def chunk_labels(chunks: list[dict[str, Any]]) -> str:
    labels = []
    for chunk in chunks:
        labels.append(f"`{chunk.get('chunk_id')}`/{chunk.get('namespace')}/{chunk.get('source_type')}")
    return ", ".join(labels)


def read_trace_jsonl(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path)
