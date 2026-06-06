from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvalItem:
    form_item_id: str
    file_name: str
    sheet_name: str
    row_index: int
    target_cell: str | None
    question_text: str | None
    instruction_text: str | None = None
    answer_example: str | None = None
    heldout_answer: str | None = None
    category_path: list[str] = field(default_factory=list)
    needs_evidence: bool = False


@dataclass(frozen=True)
class EvalResult:
    row_index: int
    generated_answer: dict[str, Any]
    judge: dict[str, Any]
    top_hits: list[dict[str, Any]] = field(default_factory=list)
