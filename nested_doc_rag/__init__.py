from __future__ import annotations

from pathlib import Path

_src_pkg = Path(__file__).resolve().parent.parent / "src" / "nested_doc_rag"
if _src_pkg.exists():
    __path__.append(str(_src_pkg))
