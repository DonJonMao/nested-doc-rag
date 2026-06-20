from __future__ import annotations

import os
from pathlib import Path

from qdrant_client import QdrantClient


def build_qdrant_client(
    *,
    qdrant_path: Path | None,
    qdrant_url: str | None = None,
    api_key_env: str | None = None,
    prefer_grpc: bool = False,
    timeout: int = 60,
) -> QdrantClient:
    url = (qdrant_url or "").strip()
    if url:
        api_key = os.environ.get((api_key_env or "").strip()) if api_key_env else None
        return QdrantClient(url=url, api_key=api_key or None, prefer_grpc=prefer_grpc, timeout=timeout)
    if qdrant_path is None:
        raise RuntimeError("qdrant_path is required when qdrant.url is not configured")
    qdrant_path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(qdrant_path), prefer_grpc=prefer_grpc, timeout=timeout)
