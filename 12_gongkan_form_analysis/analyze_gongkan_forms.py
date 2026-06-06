from __future__ import annotations

from pathlib import Path

try:
    from nested_doc_rag.form.analyze import *  # noqa: F403
    from nested_doc_rag.form.analyze import main
except ModuleNotFoundError:
    import site

    site.addsitedir(str(Path(__file__).resolve().parents[1]))
    from nested_doc_rag.form.analyze import *  # noqa: F403
    from nested_doc_rag.form.analyze import main


if __name__ == "__main__":
    main()
