from __future__ import annotations

import json
import re
from typing import Any


class JsonRepairError(ValueError):
    """Raised when model output cannot be converted into a JSON object."""


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from common chat-completion output formats."""
    candidates = candidate_json_strings(text)
    errors: list[str] = []
    for candidate in candidates:
        for repaired in [candidate, remove_trailing_commas(candidate)]:
            try:
                value = json.loads(repaired)
            except json.JSONDecodeError as exc:
                errors.append(str(exc))
                continue
            if not isinstance(value, dict):
                raise JsonRepairError(f"expected JSON object, got {type(value).__name__}")
            return value
    preview = text.replace("\n", " ")[:240]
    detail = errors[-1] if errors else "no JSON object candidate found"
    raise JsonRepairError(f"could not extract JSON object: {detail}; preview={preview!r}")


def candidate_json_strings(text: str) -> list[str]:
    stripped = text.strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1].strip())
    return dedupe(candidates)


def remove_trailing_commas(text: str) -> str:
    previous = text
    while True:
        current = re.sub(r",\s*([}\]])", r"\1", previous)
        if current == previous:
            return current
        previous = current


def dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output
