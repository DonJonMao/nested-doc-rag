from __future__ import annotations


def exact_match(expected: str | None, actual: str | None) -> bool:
    return " ".join(str(expected or "").split()) == " ".join(str(actual or "").split())
