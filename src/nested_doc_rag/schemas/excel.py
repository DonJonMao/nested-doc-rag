from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExcelWritebackItem:
    sheet_name: str
    cell: str
    value: Any
    comment: str | None = None
