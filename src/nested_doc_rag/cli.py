from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .config import load_app_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nested_doc_rag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show-config", help="Print the merged application configuration.")
    show_parser.add_argument("--config", type=Path, default=None, help="Optional local YAML config path.")
    show_parser.add_argument("--json", action="store_true", help="Print JSON. This is currently the default output.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "show-config":
        config = load_app_config(args.config)
        print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
