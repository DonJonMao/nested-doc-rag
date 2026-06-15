from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass
class AgentScopeRuntime:
    enabled: bool
    available: bool
    agentscope_version: str = ""
    reason: str = "local"
    role_agents: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def run_role(self, role_name: str, payload: dict[str, Any], execute: Callable[[], T]) -> T:
        if not self.enabled or not self.available:
            self.events.append({"event_type": "role_bypassed", "role": role_name, "runtime": "local", "reason": self.reason})
            return execute()
        role_agent = self.role_agents.setdefault(role_name, _build_agentscope_role_agent(role_name))
        _run_async_safely(
            role_agent.reply(
                _build_user_msg(
                    "step15_mas_controller",
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    {"role_name": role_name, "runtime": "agentscope"},
                ),
            ),
        )
        result = execute()
        self.events.append(
            {
                "event_type": "role_invoked",
                "role": role_name,
                "runtime": "agentscope",
                "agent_name": getattr(role_agent, "name", role_name),
            },
        )
        return result


def build_agentscope_runtime(*, enabled: bool) -> AgentScopeRuntime:
    if not enabled:
        return AgentScopeRuntime(enabled=False, available=False, reason="disabled")
    try:
        import agentscope

        version = str(getattr(agentscope, "__version__", "unknown"))
        _build_agentscope_role_agent("__probe__")
    except Exception as exc:  # noqa: BLE001 - optional dependency must degrade to local roles
        return AgentScopeRuntime(enabled=True, available=False, agentscope_version="", reason=f"agentscope unavailable: {exc}")
    return AgentScopeRuntime(enabled=True, available=True, agentscope_version=version, reason="agentscope_v2")


def _build_agentscope_role_agent(role_name: str) -> Any:
    from agentscope.agent import Agent
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock
    from agentscope.model import ChatModelBase, ChatResponse
    from pydantic import BaseModel

    class AgentScopePassthroughModel(ChatModelBase):
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
        ) -> Any:
            del model_name, messages, tools, tool_choice, kwargs
            return ChatResponse(content=[TextBlock(text="step15 role completed")], is_last=True)

    return Agent(
        name=role_name,
        system_prompt=(
            "You are a Step15 deterministic role container. "
            "Do not call tools, do not rewrite prompts, and do not make business decisions. "
            "The production result is produced by the wrapped Step15 role callback."
        ),
        model=AgentScopePassthroughModel(),
        toolkit=None,
    )


def _build_user_msg(name: str, content: str, metadata: dict[str, Any]) -> Any:
    from agentscope.message import UserMsg

    return UserMsg(name=name, content=content, metadata=metadata)


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
