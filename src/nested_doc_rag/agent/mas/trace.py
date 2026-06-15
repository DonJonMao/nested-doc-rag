from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nested_doc_rag.io import write_jsonl


@dataclass
class MASTraceEvent:
    event_type: str
    field_id: str | None
    role: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "field_id": self.field_id,
            "role": self.role,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class MASTraceRecorder:
    def __init__(self) -> None:
        self.events: list[MASTraceEvent] = []

    def record(self, field_id: str | None, role: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append(MASTraceEvent(event_type=event_type, field_id=field_id, role=role, payload=payload or {}))

    def write_jsonl(self, path: Path) -> None:
        write_jsonl(path, [event.to_dict() for event in self.events])
