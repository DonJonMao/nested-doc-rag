from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

MODES = [
    ("prompt1_baseline", ["--grounding-enabled", "--no-field-binding-enabled", "--no-parent-payload-enabled"]),
    ("prompt1_prompt2_field_binding", ["--grounding-enabled", "--field-binding-enabled", "--no-parent-payload-enabled"]),
    ("prompt1_prompt3_parent_payload", ["--grounding-enabled", "--no-field-binding-enabled", "--parent-payload-enabled"]),
    ("prompt1_prompt2_prompt3", ["--grounding-enabled", "--field-binding-enabled", "--parent-payload-enabled"]),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and compare Step15 Prompt1/Prompt2/Prompt3 ablation modes.")
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--rows", default="all")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--target-namespace", default=None)
    parser.add_argument("--global-namespace", default=None)
    parser.add_argument("--room-context", default=None)
    parser.add_argument("--form-items", type=Path, default=None)
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--use-judge-cache", action="store_true")
    parser.add_argument("--extra-arg", action="append", default=[], help="Extra argument forwarded to run-step15-agent. Repeat as needed.")
    args = parser.parse_args()

    args.out_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for mode_name, mode_args in MODES:
        out_dir = args.out_root / mode_name
        command = build_command(args, out_dir, mode_args)
        subprocess.run(command, check=True)
        rows.append({"mode": mode_name, **load_metrics(out_dir)})

    comparison = {"runs": rows}
    (args.out_root / "comparison.json").write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print_table(rows)


def build_command(args: argparse.Namespace, out_dir: Path, mode_args: list[str]) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "nested_doc_rag.cli",
        "run-step15-agent",
        "--rows",
        args.rows,
        "--retrieval-plan",
        "layered",
        "--out-dir",
        str(out_dir),
    ]
    if args.config:
        command.extend(["--config", str(args.config)])
    if args.target_namespace:
        command.extend(["--target-namespace", args.target_namespace])
    if args.global_namespace:
        command.extend(["--global-namespace", args.global_namespace])
    if args.room_context:
        command.extend(["--room-context", args.room_context])
    if args.form_items:
        command.extend(["--form-items", str(args.form_items)])
    command.append("--judge" if args.judge else "--no-judge")
    if args.use_judge_cache:
        command.append("--use-judge-cache")
    command.extend(mode_args)
    command.extend(args.extra_arg)
    return command


def load_metrics(out_dir: Path) -> dict[str, Any]:
    summary = read_json(out_dir / "summary.json")
    manifest = read_json(out_dir / "run_manifest.json")
    trace_summary = summary.get("trace_summary") or {}
    counts = manifest.get("counts") or {}
    return {
        "acceptable+": summary.get("acceptable_or_better", 0),
        "partial+": summary.get("partial_or_better", 0),
        "avg_score": summary.get("average_score", 0),
        "failed": counts.get("failed", 0),
        "answered_count": counts.get("answered", 0),
        "partial_clue_count": counts.get("partial_clue", 0),
        "not_found_count": counts.get("not_found", 0),
        "writeback_allowed_count": counts.get("writeback_allowed", 0),
        "review_required_count": counts.get("review_required", 0),
        "evidence_strength_distribution": trace_summary.get("evidence_strength_distribution", {}),
        "field_binding_distribution": trace_summary.get("field_binding_distribution", {}),
        "manifest_status": manifest.get("status"),
        "out_dir": str(out_dir),
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def print_table(rows: list[dict[str, Any]]) -> None:
    headers = [
        "mode",
        "acceptable+",
        "partial+",
        "avg_score",
        "failed",
        "answered_count",
        "partial_clue_count",
        "not_found_count",
        "writeback_allowed_count",
        "review_required_count",
        "manifest_status",
    ]
    print("\t".join(headers))
    for row in rows:
        print("\t".join(str(row.get(header, "")) for header in headers))


if __name__ == "__main__":
    main()
