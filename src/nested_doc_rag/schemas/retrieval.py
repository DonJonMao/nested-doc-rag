from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str | None
    namespace: str | None
    corpus_layer: str | None
    source_type: str | None
    text_for_embedding: str
    raw_text: str | None = None
    vector_rank: int | None = None
    vector_score: float | None = None
    rerank_rank: int | None = None
    rerank_score: float | None = None
    file_name: str | None = None
    anchor: str | None = None
    proof_attachment_ids: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LayeredRetrievalSpec:
    layer_name: str
    description: str
    namespaces: str
    corpus_layers: list[str]
    source_types: list[str]
    vector_top_k: int
    rerank_top_n: int
