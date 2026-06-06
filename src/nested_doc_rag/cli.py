from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .config import load_app_config
from .evaluation.experiment_runner import run_baseline_experiment
from .evaluation.field_metrics import evaluate_fields_from_files
from .excel.writeback import writeback_from_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nested_doc_rag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show-config", help="Print the merged application configuration.")
    show_parser.add_argument("--config", type=Path, default=None, help="Optional local YAML config path.")
    show_parser.add_argument("--json", action="store_true", help="Print JSON. This is currently the default output.")

    eval_parser = subparsers.add_parser("eval-fields", help="Evaluate field-level gongkan predictions.")
    eval_parser.add_argument("--gold", type=Path, required=True, help="Gold field JSONL path.")
    eval_parser.add_argument("--pred", type=Path, required=True, help="Prediction field JSONL path.")
    eval_parser.add_argument("--out-dir", type=Path, required=True, help="Directory for field evaluation reports.")
    eval_parser.add_argument("--evidence-k", type=int, default=5, help="k for evidence_recall@k.")
    eval_parser.add_argument("--human-review-threshold", type=float, default=0.55, help="Confidence below this threshold needs review.")

    baseline_parser = subparsers.add_parser("run-baselines", help="Run form-filling RAG baseline experiments.")
    baseline_parser.add_argument("--config", type=Path, required=True, help="Experiment YAML config path.")
    baseline_parser.add_argument("--out-dir", type=Path, default=None, help="Override output directory.")
    baseline_parser.add_argument("--no-resume", action="store_true", help="Recompute predictions even if checkpoints exist.")

    writeback_parser = subparsers.add_parser("writeback", help="Write field predictions back to an Excel workbook.")
    writeback_parser.add_argument("--template", type=Path, required=True, help="Source Excel template path.")
    writeback_parser.add_argument("--pred", type=Path, required=True, help="FieldPrediction JSONL path.")
    writeback_parser.add_argument("--out", type=Path, required=True, help="Filled Excel output path.")
    writeback_parser.add_argument("--trace", type=Path, default=None, help="Optional trace JSONL path.")
    writeback_parser.add_argument("--evidence-map", type=Path, default=None, help="Optional input evidence map JSON path.")
    writeback_parser.add_argument("--mode", choices=["safe", "overwrite"], default="safe", help="Write mode.")
    writeback_parser.add_argument("--no-comments", action="store_true", help="Disable Excel cell comments.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "show-config":
        config = load_app_config(args.config)
        print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))
    elif args.command == "eval-fields":
        result = evaluate_fields_from_files(
            gold_path=args.gold,
            pred_path=args.pred,
            out_dir=args.out_dir,
            evidence_k=args.evidence_k,
            human_review_threshold=args.human_review_threshold,
        )
        print(
            json.dumps(
                {
                    "field_count": result.metrics["field_count"],
                    "field_semantic_match": result.metrics["field_semantic_match"],
                    "correction_required_rate": result.metrics["correction_required_rate"],
                    "out_dir": str(args.out_dir),
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "writeback":
        summary = writeback_from_files(
            template_path=args.template,
            predictions_path=args.pred,
            output_path=args.out,
            trace_path=args.trace,
            evidence_map_path=args.evidence_map,
            mode=args.mode,
            write_comments=not args.no_comments,
        )
        print(json.dumps(summary.to_dict(), ensure_ascii=False))
    elif args.command == "run-baselines":
        summary = run_baseline_experiment(
            args.config,
            out_dir=args.out_dir,
            resume=False if args.no_resume else None,
        )
        print(
            json.dumps(
                {
                    "method_count": len(summary["methods"]),
                    "target_namespace": summary["target_namespace"],
                    "out_dir": summary["output_dir"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
