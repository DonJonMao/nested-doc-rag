from __future__ import annotations

from nested_doc_rag.schemas.agent import AgentTraceEvent


def make_trace_event(event_id: str, event_type: str, message: str) -> AgentTraceEvent:
    return AgentTraceEvent(event_id=event_id, event_type=event_type, message=message)
