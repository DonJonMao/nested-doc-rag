from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocumentRecord:
    file_id: str
    file_name: str
    relative_path: str
    document_role: str
    suffix: str
    data_center_id: str = "global"
    source_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
