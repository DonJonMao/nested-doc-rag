from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from agentscope.agent import Agent
from agentscope.credential import CredentialBase
from agentscope.message import TextBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from pydantic import BaseModel

T = TypeVar("T")


class AgentScopePassthroughModel(ChatModelBase):
    """Local model used only to drive AgentScope agent lifecycle events."""

    class Parameters(BaseModel):
        pass

    def __init__(self) -> None:
        super().__init__(
            credential=CredentialBase(name="local_step15_mas_runtime"),
            model="step15-local-passthrough",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
        )

    async def _call_api(
        self,
        model_name: str,
        messages: list[Any],
        tools: list[dict] | None = None,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        del model_name, messages, tools, tool_choice, kwargs
        return ChatResponse(content=[TextBlock(text="step15 role completed")], is_last=True)


@dataclass
class AgentScopeRoleAgent:
    role_name: str
    agent: Agent = field(init=False)
    invocations: int = 0

    def __post_init__(self) -> None:
        self.agent = Agent(
            name=self.role_name,
            system_prompt=(
                "You are a Step15 deterministic role container. "
                "Do not call tools, do not rewrite prompts, and do not make business decisions. "
                "The production result is produced by the wrapped Step15 role callback."
            ),
            model=AgentScopePassthroughModel(),
            toolkit=None,
        )

    def run(self, *, payload: dict[str, Any], execute: Callable[[], T]) -> T:
        self.invocations += 1
        _run_async_safely(
            self.agent.reply(
                UserMsg(
                    name="step15_mas_controller",
                    content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    metadata={"role_name": self.role_name, "runtime": "agentscope"},
                ),
            ),
        )
        return execute()


@dataclass
class AgentScopeRuntime:
    enabled: bool
    agentscope_version: str
    reason: str = "agentscope"
    role_agents: dict[str, AgentScopeRoleAgent] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.enabled

    def run_role(self, role_name: str, payload: dict[str, Any], execute: Callable[[], T]) -> T:
        if not self.enabled:
            self.events.append({"event_type": "role_bypassed", "role": role_name})
            return execute()
        role_agent = self.role_agents.setdefault(role_name, AgentScopeRoleAgent(role_name=role_name))
        result = role_agent.run(payload=payload, execute=execute)
        self.events.append(
            {
                "event_type": "role_invoked",
                "role": role_name,
                "runtime": "agentscope",
                "agent_name": role_agent.agent.name,
                "invocations": role_agent.invocations,
            },
        )
        return result


def build_agentscope_runtime(*, enabled: bool) -> AgentScopeRuntime:
    import agentscope

    version = str(getattr(agentscope, "__version__", "unknown"))
    return AgentScopeRuntime(enabled=enabled, agentscope_version=version, reason="agentscope_v2")


def _run_async_safely(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def run_in_thread() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - propagate exceptions across thread boundary
            result["error"] = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")
