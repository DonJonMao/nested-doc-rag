from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


KIND_CHAT = "chat"
KIND_EMBEDDING = "embedding"
KIND_RERANK = "rerank"


@dataclass(frozen=True)
class GatewayContext:
    enabled: bool
    base_url: str
    token: str
    run_id: str
    job_id: str
    user_id: str
    workspace_id: str


def is_enabled() -> bool:
    return os.environ.get("NDR_MODEL_GATEWAY_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def context_from_env() -> GatewayContext:
    return GatewayContext(
        enabled=is_enabled(),
        base_url=os.environ.get("NDR_MODEL_GATEWAY_BASE_URL", "").strip().rstrip("/"),
        token=os.environ.get("NDR_MODEL_GATEWAY_TOKEN", "").strip(),
        run_id=os.environ.get("NDR_RUN_ID", "").strip() or "unknown",
        job_id=os.environ.get("NDR_JOB_ID", "").strip(),
        user_id=os.environ.get("NDR_USER_ID", "").strip(),
        workspace_id=os.environ.get("NDR_WORKSPACE_ID", "").strip(),
    )


def endpoint_for(kind: str, original_url: str) -> str:
    ctx = context_from_env()
    if not ctx.enabled:
        return original_url
    if not ctx.base_url:
        raise RuntimeError("NDR_MODEL_GATEWAY_BASE_URL is required when model gateway is enabled")
    if kind == KIND_CHAT:
        return f"{ctx.base_url}/v1/chat/completions"
    if kind == KIND_EMBEDDING:
        return f"{ctx.base_url}/v1/embeddings"
    if kind == KIND_RERANK:
        return f"{ctx.base_url}/v1/rerank"
    raise ValueError(f"unsupported model gateway kind: {kind}")


def headers_for(
    kind: str,
    purpose: str,
    *,
    field_id: str | None = None,
    extra: dict[str, str] | None = None,
    request_id: str | None = None,
) -> dict[str, str] | None:
    ctx = context_from_env()
    if not ctx.enabled:
        return extra
    headers: dict[str, str] = dict(extra or {})
    if ctx.token:
        headers["Authorization"] = f"Bearer {ctx.token}"
    headers["X-NDR-Request-ID"] = request_id or str(uuid4())
    headers["X-NDR-Run-ID"] = ctx.run_id
    headers["X-NDR-Model-Kind"] = kind
    headers["X-NDR-Model-Purpose"] = purpose
    if field_id:
        headers["X-NDR-Field-ID"] = field_id
    if ctx.job_id:
        headers["X-NDR-Job-ID"] = ctx.job_id
    if ctx.user_id:
        headers["X-NDR-User-ID"] = ctx.user_id
    if ctx.workspace_id:
        headers["X-NDR-Workspace-ID"] = ctx.workspace_id
    return headers


def request_options(
    kind: str,
    original_url: str,
    purpose: str,
    *,
    field_id: str | None = None,
    direct_headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, str] | None]:
    if not is_enabled():
        return original_url, direct_headers
    return endpoint_for(kind, original_url), headers_for(kind, purpose, field_id=field_id)


def gateway_error_request_id(error: Any) -> str:
    if isinstance(error, dict):
        value = error.get("request_id")
        nested = error.get("error")
        if not value and isinstance(nested, dict):
            value = nested.get("request_id")
        return str(value or "")
    return ""
