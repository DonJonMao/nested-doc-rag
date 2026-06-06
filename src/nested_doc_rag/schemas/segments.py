from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SegmentRecord:
    segment_id: str
    source_type: str
    namespace: str
    raw_text: str
    text_for_embedding: str
    corpus_layer: str = "fact"
    file_name: str | None = None
    anchor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
