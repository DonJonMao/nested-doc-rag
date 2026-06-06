from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentAction:
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTraceEvent:
    event_id: str
    event_type: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
