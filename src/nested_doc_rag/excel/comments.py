from __future__ import annotations


def format_source_comment(source: str, note: str | None = None) -> str:
    return source if not note else f"{source}\n{note}"
