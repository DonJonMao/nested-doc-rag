from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentScopeRuntime:
    available: bool
    reason: str = ""

    def wrap_role_result(self, role_name: str, result: Any) -> Any:
        del role_name
        return result


def build_agentscope_runtime(*, enabled: bool) -> AgentScopeRuntime:
    if not enabled:
        return AgentScopeRuntime(available=False, reason="disabled")
    try:
        import agentscope  # type: ignore[import-not-found]  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - optional runtime must gracefully fall back
        return AgentScopeRuntime(available=False, reason=f"agentscope unavailable: {exc}")
    return AgentScopeRuntime(available=True)
