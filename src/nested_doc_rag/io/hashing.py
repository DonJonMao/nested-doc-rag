from __future__ import annotations

import hashlib
from typing import Any


def stable_hash(*parts: Any, length: int = 16) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def stable_id(prefix: str, *parts: Any, length: int = 16) -> str:
    return f"{prefix}_{stable_hash(*parts, length=length)}"
