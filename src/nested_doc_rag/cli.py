from __future__ import annotations

import argparse
import json
import os
import warnings
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from nested_doc_rag.agent.backends import (
    DeterministicAnswerGenerator,
    LayeredQdrantEvidenceRetriever,
    LLMAnswerGenerator,
    MiniCorpusRetriever,
    QdrantEvidenceRetriever,
)
from nested_doc_rag.agent.step15_runner import Step15AgentRunner, parse_rows_arg, validate_step15_agent_config
from nested_doc_rag.artifacts import ArtifactValidationError, validate_step15_artifacts
from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.form.input_snapshot import (
    build_acquisition_contract,
    build_form_input_snapshot,
    persist_form_input_snapshot,
    serialized_form_items,
    validate_form_input_snapshot,
)
from nested_doc_rag.gongkan_eval import BASE_CLOUD_FILE, select_form_items
from nested_doc_rag.ingestion import IngestionOptions, dumps_summary, run_knowledge_ingestion
from nested_doc_rag.io import read_jsonl, write_json
from nested_doc_rag.retrieval import QdrantRetriever

from .agent.runner import FieldFillingAgent, load_corpus, load_fields
from .config import load_app_config, normalize_existing_value_policy
from .evaluation.experiment_runner import run_baseline_experiment
from .evaluation.field_metrics import evaluate_fields_from_files
from .excel.writeback import writeback_from_files


def require_namespace(parser: argparse.ArgumentParser, value: str | None, option_name: str) -> str:
    namespace = (value or "").strip()
    if not namespace:
        parser.error(f"{option_name} is required")
    return namespace


class LegacyFormItemsFallbackWarning(UserWarning):
    """A replay run used historical Step12 fields instead of an uploaded form."""


def prepare_step15_form_input(args: argparse.Namespace, config: Any, target_namespace: str, global_namespace: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if args.form_items is not None:
        if not args.form_items.is_file():
            raise RuntimeError(f"form items file does not exist: {args.form_items}")
        all_items = read_jsonl(args.form_items)
        report = {"parser_name": "explicit-form-items", "parser_version": "form-items-v1", "input_mode": "explicit_items"}
    elif args.template is not None:
        from nested_doc_rag.form.template_parser import parse_form_template

        result = parse_form_template(args.template, include_heldout_answers=bool(args.judge))
        all_items, report = result.items, dict(result.report)
        report["input_mode"] = "template"
    else:
        warnings.warn("No template or explicit form items supplied; using historical Step12 fields for compatibility.", LegacyFormItemsFallbackWarning, stacklevel=2)
        legacy_path = config.paths.artifacts_dir / "12_gongkan_form_analysis" / "form_items.jsonl"
        if not legacy_path.is_file():
            raise RuntimeError(f"legacy form items file does not exist: {legacy_path}; provide --template or --form-items")
        all_items = [item for item in read_jsonl(legacy_path) if item.get("file_name") == BASE_CLOUD_FILE]
        report = {"parser_name": "legacy-step12", "parser_version": "legacy-form-items-v1", "input_mode": "legacy"}
    all_items = select_form_items(all_items, None)
    selected = select_form_items(all_items, parse_rows_arg(args.rows))
    snapshot = build_form_input_snapshot(
        all_items, selected_items=selected, template_path=args.template,
        input_mode=report["input_mode"], parser_name=report["parser_name"], parser_version=report["parser_version"],
        rows_spec=args.rows, target_namespace=target_namespace, global_namespace=global_namespace,
        room_context=args.room_context,
        acquisition_contract=build_acquisition_contract(
            config, prompt_version=args.prompt_version or "step15_compat",
            collection_name=args.qdrant_collection or config.qdrant.collection_name,
            overwrite_all_cli=getattr(args, "existing_value_policy", None) == "overwrite_all",
            index_scopes=getattr(args, "normalized_index_scopes", None),
        ),
    )
    if report.get("template_sha256") and str(report["template_sha256"]).removeprefix("sha256:") != snapshot["template_sha256"]:
        raise RuntimeError("template changed during parsing; start again with a stable uploaded file")
    validate_form_input_snapshot(args.out_dir, snapshot, resume=bool(args.resume))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "form_items.jsonl").write_text(serialized_form_items(all_items), encoding="utf-8")
    report["detected_fields"] = len(all_items)
    report["selected_field_count"] = len(selected)
    write_json(args.out_dir / "form_parse_report.json", report)
    persist_form_input_snapshot(args.out_dir, snapshot, resume=bool(args.resume))
    return selected, snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nested_doc_rag")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show-config", help="Print the merged application configuration.")
    show_parser.add_argument("--config", type=Path, default=None, help="Optional local YAML config path.")
    show_parser.add_argument("--json", action="store_true", help="Print JSON. This is currently the default output.")

    form_parser = subparsers.add_parser("parse-form-template", help="Parse an uploaded workbook into runtime form fields.")
    form_parser.add_argument("--template", type=Path, required=True)
    form_parser.add_argument("--out", type=Path, required=True, help="Output form_items.jsonl path.")
    form_parser.add_argument("--report", type=Path, default=None, help="Optional diagnostics JSON path.")

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
    writeback_parser.add_argument("--retrieval-evidence", type=Path, default=None, help="Retrieved authority JSONL; defaults beside predictions.")
    writeback_parser.add_argument("--mode", choices=["safe", "overwrite"], default="safe", help="Write mode.")
    writeback_parser.add_argument("--existing-value-policy", choices=["preserve", "overwrite_confirmed", "overwrite_all"], default="preserve", help="Existing-cell policy; all policies protect formulas and evidence gates.")
    writeback_parser.add_argument("--no-comments", action="store_true", help="Disable Excel cell comments.")

    artifacts_parser = subparsers.add_parser("validate-artifacts", help="Validate a frozen Step15AgentRunner artifact directory.")
    artifacts_parser.add_argument("--run-dir", type=Path, required=True, help="Step15AgentRunner output directory.")
    artifacts_parser.add_argument(
        "--allow-mutated-predictions",
        action="store_true",
        help="Allow predictions.jsonl to differ from predictions_raw.jsonl. Disabled for overlay mode.",
    )

    ingest_parser = subparsers.add_parser("ingest-knowledge", help="Parse uploaded knowledge documents, embed chunks, and upsert them to Qdrant.")
    ingest_parser.add_argument("--config", type=Path, default=None, help="Optional local YAML config path.")
    ingest_parser.add_argument("--input-dir", type=Path, required=True, help="Directory containing materialized knowledge documents.")
    ingest_parser.add_argument("--namespace", required=True, help="Knowledge-base namespace to write into Qdrant.")
    ingest_parser.add_argument("--knowledge-base-id", required=True, help="Stable knowledge-base id for traceable chunk ids.")
    ingest_parser.add_argument("--out-dir", type=Path, required=True, help="Directory for ingestion artifacts.")
    ingest_parser.add_argument("--qdrant-collection", default=None, help="Qdrant collection override.")
    ingest_parser.add_argument("--qdrant-namespace", default=None, help="Qdrant namespace override. Defaults to --namespace.")
    ingest_parser.add_argument("--batch-size", type=int, default=16, help="Embedding/upsert batch size.")
    ingest_parser.add_argument("--index-version-id", default=None, help="Immutable candidate index UUID.")
    ingest_parser.add_argument("--input-snapshot", type=Path, default=None, help="Frozen candidate source manifest.")
    ingest_parser.add_argument("--input-snapshot-hash", default=None, help="SHA256 of the exact input snapshot bytes.")
    ingest_parser.add_argument("--resume", action="store_true", help="Retry an immutable candidate; only its exact scope can be rebuilt.")

    agent_parser = subparsers.add_parser("run-agent", help="Run the lightweight field-filling agent with mini or real backends.")
    agent_parser.add_argument("--config", type=Path, default=None, help="Optional local YAML config path.")
    agent_parser.add_argument("--gold", type=Path, default=None, help="FieldGold JSONL path. Kept for eval-compatible mini demos.")
    agent_parser.add_argument("--fields", type=Path, default=None, help="Field input JSONL path. Preferred for real runs.")
    agent_parser.add_argument("--corpus", type=Path, default=None, help="Mini corpus JSONL path.")
    agent_parser.add_argument("--target-namespace", default=None, help="Target namespace for field retrieval.")
    agent_parser.add_argument("--out-dir", type=Path, required=True, help="Run output directory.")
    agent_parser.add_argument("--room-context", default=None, help="Optional known room context.")
    agent_parser.add_argument("--template", type=Path, default=None, help="Optional Excel template for writeback.")
    agent_parser.add_argument("--max-repair-attempts", type=int, default=1, help="Maximum repair attempts per field. Capped at 1.")
    agent_parser.add_argument("--no-writeback", action="store_true", help="Disable Excel writeback even when a template is provided.")
    agent_parser.add_argument("--trace-format", default="md,jsonl", help="Accepted for compatibility; both md and jsonl are written.")
    agent_parser.add_argument("--retrieval-backend", choices=["mini", "qdrant"], default=None, help="Evidence retrieval backend.")
    agent_parser.add_argument("--retrieval-plan", default=None, help="Qdrant retrieval plan for run-agent.")
    agent_parser.add_argument("--generation-backend", choices=["deterministic", "llm"], default=None, help="Answer generation backend.")
    agent_parser.add_argument("--enable-rerank", action="store_true", help="Enable rerank for qdrant retrieval.")
    agent_parser.add_argument("--qdrant-path", type=Path, default=None, help="Qdrant local path.")
    agent_parser.add_argument("--qdrant-collection", default=None, help="Qdrant collection name.")
    agent_parser.add_argument("--embedding-endpoint", default=None, help="Embedding service endpoint.")
    agent_parser.add_argument("--embedding-model", default=None, help="Embedding model name.")
    agent_parser.add_argument("--rerank-endpoint", default=None, help="Rerank service endpoint.")
    agent_parser.add_argument("--rerank-model", default=None, help="Rerank model name.")
    agent_parser.add_argument("--chat-endpoint", default=None, help="OpenAI-compatible chat completion endpoint.")
    agent_parser.add_argument("--chat-model", default=None, help="Chat model name.")
    agent_parser.add_argument("--chat-api-key-env", default=None, help="Environment variable containing chat API key.")
    agent_parser.add_argument("--vector-top-k", type=int, default=None, help="Vector retrieval top-k.")
    agent_parser.add_argument("--rerank-top-n", type=int, default=None, help="Rerank top-n.")
    agent_parser.add_argument("--resume", action="store_true", help="Resume from field-level checkpoints in out-dir.")
    agent_parser.add_argument("--checkpoint-every", type=int, default=1, help="Write a checkpoint after this many completed fields.")
    agent_parser.add_argument("--checkpoint-path", type=Path, default=None, help="Optional predictions checkpoint JSONL path.")

    step15_agent_parser = subparsers.add_parser("run-step15-agent", help="Run Step 15 layered RAG inside an Agentic runtime.")
    step15_agent_parser.add_argument("--config", type=Path, default=None, help="Optional local YAML config path.")
    step15_agent_parser.add_argument("--target-namespace", default=None, help="Target namespace.")
    step15_agent_parser.add_argument("--global-namespace", default=None, help="Global/reference namespace.")
    step15_agent_parser.add_argument("--room-context", default=None, help="Known target room context for disambiguation.")
    step15_agent_parser.add_argument("--rows", default="all", help="Rows to run: all, 4-144, or 34,38,42.")
    step15_agent_parser.add_argument("--form-items", type=Path, default=None, help="Optional form_items.jsonl override.")
    step15_agent_parser.add_argument("--retrieval-plan", choices=["layered"], default=None, help="Step 15 retrieval plan. Production uses layered.")
    sufficiency_group = step15_agent_parser.add_mutually_exclusive_group()
    sufficiency_group.add_argument("--sufficiency-enabled", dest="sufficiency_enabled", action="store_true", default=None, help="Use semantic sufficiency and at most one targeted supplement.")
    sufficiency_group.add_argument("--no-sufficiency-enabled", dest="sufficiency_enabled", action="store_false", help="Explicit legacy layered retrieval for compatibility or ablation.")
    schema_group = step15_agent_parser.add_mutually_exclusive_group()
    schema_group.add_argument("--schema-first-enabled", dest="schema_first_enabled", action="store_true", default=None, help="Select field schemas before retrieving Excel values (A4).")
    schema_group.add_argument("--no-schema-first-enabled", dest="schema_first_enabled", action="store_false", help="Disable the independent schema-to-value retrieval variant.")
    grounding_group = step15_agent_parser.add_mutually_exclusive_group()
    grounding_group.add_argument("--grounding-enabled", dest="grounding_enabled", action="store_true", default=None, help="Enable evidence strength overlay gate.")
    grounding_group.add_argument("--no-grounding-enabled", dest="grounding_enabled", action="store_false", help="Disable evidence strength overlay gate.")
    field_binding_group = step15_agent_parser.add_mutually_exclusive_group()
    field_binding_group.add_argument("--field-binding-enabled", dest="field_binding_enabled", action="store_true", default=None, help="Enable Prompt 2 field-level schema binding gate.")
    field_binding_group.add_argument("--no-field-binding-enabled", dest="field_binding_enabled", action="store_false", help="Disable Prompt 2 field-level schema binding gate.")
    parent_payload_group = step15_agent_parser.add_mutually_exclusive_group()
    parent_payload_group.add_argument("--parent-payload-enabled", dest="parent_payload_enabled", action="store_true", default=None, help="Enable Prompt 3 compact parent payload evidence context.")
    parent_payload_group.add_argument("--no-parent-payload-enabled", dest="parent_payload_enabled", action="store_false", help="Disable Prompt 3 compact parent payload evidence context.")
    step15_agent_parser.add_argument(
        "--prompt-version",
        choices=["step15_compat", "agent_v2", "agentic_v1"],
        default="step15_compat",
        help="Answer prompt version. step15_compat preserves the Step 15 effect prompt.",
    )
    step15_agent_parser.add_argument("--agentic-mas", action="store_true", help="Run Step 15 with agentic evidence-replanning MAS.")
    step15_agent_parser.add_argument("--agentic-max-rounds", type=int, default=None, help="Override agentic_mas.max_rounds.")
    step15_agent_parser.add_argument("--disable-missing-info", action="store_true", help="Disable the missing_info agentic workflow.")
    step15_agent_parser.add_argument("--disable-wrong-answer-risk", action="store_true", help="Disable the wrong_answer_risk agentic workflow.")
    step15_agent_parser.add_argument("--disable-not-found-recovery", action="store_true", help="Disable the not_found_recovery agentic workflow.")
    step15_agent_parser.add_argument("--disable-uncertainty-conflict", action="store_true", help="Disable the uncertainty_conflict agentic workflow.")
    step15_agent_parser.add_argument("--vector-top-k", type=int, default=None, help="Vector retrieval top-k.")
    step15_agent_parser.add_argument("--rerank-top-n", type=int, default=None, help="Rerank top-n.")
    judge_group = step15_agent_parser.add_mutually_exclusive_group()
    judge_group.add_argument("--judge", dest="judge", action="store_true", default=False, help="Run heldout-answer judge.")
    judge_group.add_argument("--no-judge", dest="judge", action="store_false", help="Disable judge. This is production mode.")
    step15_agent_parser.add_argument("--resume", action="store_true", help="Resume from field-level checkpoints in out-dir.")
    step15_agent_parser.add_argument("--checkpoint-every", type=int, default=1, help="Write checkpoint every N fields.")
    step15_agent_parser.add_argument("--template", type=Path, default=None, help="Uploaded Excel template used to parse runtime fields and optional writeback.")
    step15_agent_parser.add_argument("--writeback", action="store_true", help="Enable safe Excel writeback.")
    step15_agent_parser.add_argument("--out-dir", type=Path, required=True, help="Run output directory.")
    step15_agent_parser.add_argument("--existing-value-policy", choices=["preserve", "overwrite_confirmed", "overwrite_all"], default=None, help="Explicit existing-cell policy; overwrite_all is available only through this CLI option.")
    step15_agent_parser.add_argument("--qdrant-path", type=Path, default=None, help="Qdrant local path.")
    step15_agent_parser.add_argument("--qdrant-collection", default=None, help="Qdrant collection name.")
    step15_agent_parser.add_argument("--index-scopes", type=Path, default=None, help="JSON file fixing one KB/version per requested namespace.")
    step15_agent_parser.add_argument("--embedding-endpoint", default=None, help="Embedding service endpoint.")
    step15_agent_parser.add_argument("--embedding-model", default=None, help="Embedding model name.")
    step15_agent_parser.add_argument("--rerank-endpoint", default=None, help="Rerank service endpoint.")
    step15_agent_parser.add_argument("--rerank-model", default=None, help="Rerank model name.")
    step15_agent_parser.add_argument("--chat-endpoint", default=None, help="DeepSeek/OpenAI-compatible chat completion endpoint.")
    step15_agent_parser.add_argument("--chat-model", default=None, help="Chat model name.")
    step15_agent_parser.add_argument("--chat-api-key-env", default=None, help="Environment variable containing chat API key.")
    step15_agent_parser.add_argument("--deepseek-api-key-env", default=None, help="Alias for --chat-api-key-env.")
    step15_agent_parser.add_argument("--deepseek-api-key", default=None, help="Optional direct chat API key. Prefer env vars for real runs.")
    step15_agent_parser.add_argument("--timeout", type=int, default=None, help="HTTP timeout seconds.")
    step15_agent_parser.add_argument("--chat-max-retries", type=int, default=5, help="Maximum chat/service retries.")
    step15_agent_parser.add_argument("--chat-retry-backoff-seconds", type=int, default=3, help="Seconds to wait between chat retries.")
    step15_agent_parser.add_argument("--judge-cache", type=Path, default=None, help="Judge cache JSONL path.")
    judge_cache_group = step15_agent_parser.add_mutually_exclusive_group()
    judge_cache_group.add_argument("--use-judge-cache", dest="use_judge_cache", action="store_true", default=False, help="Reuse cached judge results.")
    judge_cache_group.add_argument("--no-judge-cache", dest="use_judge_cache", action="store_false", help="Disable judge cache.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "show-config":
        config = load_app_config(args.config)
        print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2))
    elif args.command == "parse-form-template":
        from nested_doc_rag.form.template_parser import parse_form_template

        report_path = args.report or args.out.parent / "form_parse_report.json"
        try:
            result = parse_form_template(args.template)
        except (RuntimeError, ValueError) as exc:
            if getattr(exc, "report", None) is not None:
                write_json(report_path, exc.report)
            parser.error(str(exc))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized_form_items(result.items), encoding="utf-8")
        write_json(report_path, result.report)
        print(json.dumps({"detected_fields": len(result.items), "form_items": str(args.out), "report": str(report_path)}, ensure_ascii=False))
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
            retrieval_evidence_path=args.retrieval_evidence,
            mode=args.mode,
            writeback_config={"existing_value_policy": args.existing_value_policy},
            overwrite_all_cli=args.existing_value_policy == "overwrite_all",
            write_comments=not args.no_comments,
        )
        print(json.dumps(summary.to_dict(), ensure_ascii=False))
    elif args.command == "validate-artifacts":
        try:
            result = validate_step15_artifacts(args.run_dir, allow_mutated_predictions=bool(args.allow_mutated_predictions))
        except ArtifactValidationError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "ingest-knowledge":
        config = load_app_config(args.config)
        try:
            summary = run_knowledge_ingestion(
                IngestionOptions(
                    input_dir=args.input_dir,
                    namespace=args.namespace,
                    knowledge_base_id=args.knowledge_base_id,
                    out_dir=args.out_dir,
                    config=config,
                    qdrant_collection=args.qdrant_collection,
                    qdrant_namespace=args.qdrant_namespace,
                    batch_size=args.batch_size,
                    resume=bool(args.resume),
                    index_version_id=args.index_version_id,
                    input_snapshot_path=args.input_snapshot,
                    input_snapshot_hash=args.input_snapshot_hash,
                )
            )
        except RuntimeError as exc:
            parser.error(str(exc))
        print(dumps_summary(summary))
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
    elif args.command == "run-agent":
        config = load_app_config(args.config)
        fields_path = args.fields or args.gold
        if not fields_path:
            parser.error("run-agent requires --fields or --gold")
        retrieval_backend = args.retrieval_backend or config.agent.retrieval_backend
        generation_backend = args.generation_backend or config.agent.generation_backend
        retrieval_plan = args.retrieval_plan or (config.retrieval.plan if retrieval_backend == "qdrant" else "flat")
        target_namespace = require_namespace(parser, args.target_namespace or config.retrieval.target_namespace, "--target-namespace")
        enable_rerank = bool(args.enable_rerank or config.agent.enable_rerank)
        vector_top_k = args.vector_top_k or config.retrieval.vector_top_k
        rerank_top_n = args.rerank_top_n or config.retrieval.rerank_top_n
        try:
            retriever = build_agent_retriever(
                args,
                config,
                retrieval_backend,
                retrieval_plan,
                target_namespace,
                enable_rerank,
                vector_top_k,
                rerank_top_n,
            )
            generator = build_agent_generator(args, config, generation_backend)
        except RuntimeError as exc:
            parser.error(str(exc))
        agent = FieldFillingAgent(
            target_namespace=target_namespace,
            corpus=load_corpus(args.corpus) if args.corpus else [],
            out_dir=args.out_dir,
            config=config,
            room_context=args.room_context,
            max_repair_attempts=args.max_repair_attempts,
            template_path=args.template,
            writeback_enabled=not args.no_writeback,
            retriever=retriever,
            answer_generator=generator,
            retrieval_backend=retrieval_backend,
            generation_backend=generation_backend,
            enable_rerank=enable_rerank,
            resume=args.resume,
            checkpoint_every=args.checkpoint_every,
            checkpoint_path=args.checkpoint_path,
        )
        predictions = agent.run(load_fields(fields_path))
        print(
            json.dumps(
                {
                    "field_count": len(predictions),
                    "out_dir": str(args.out_dir),
                    "run_id": agent.run_id,
                    "writeback": agent.writeback_status,
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "run-step15-agent":
        config = load_app_config(args.config, cli_overrides=step15_agentic_cli_overrides(args))
        try:
            if args.existing_value_policy is not None:
                policy = normalize_existing_value_policy(args.existing_value_policy, allow_overwrite_all=True)
                config = replace(config, writeback=replace(config.writeback, existing_value_policy=policy))
            target_namespace = require_namespace(parser, args.target_namespace or config.retrieval.target_namespace, "--target-namespace")
            global_namespace = require_namespace(parser, args.global_namespace or config.retrieval.global_namespace, "--global-namespace")
            if target_namespace == global_namespace:
                parser.error("--global-namespace must differ from --target-namespace")
            qdrant_path = args.qdrant_path or config.paths.qdrant_path
            collection_name = args.qdrant_collection or config.qdrant.collection_name
            from nested_doc_rag.retrieval.version_scope import normalize_index_scopes
            args.normalized_index_scopes = normalize_index_scopes(
                json.loads(args.index_scopes.read_text(encoding="utf-8")) if args.index_scopes is not None else None,
                collection_name=collection_name, namespaces=[target_namespace, global_namespace],
            )
            if args.normalized_index_scopes is not None and {scope["namespace"] for scope in args.normalized_index_scopes} != {target_namespace, global_namespace}:
                raise ValueError("fill index scopes must contain exactly the target and global namespaces")
            embedding_endpoint = args.embedding_endpoint or config.services.embedding_endpoint
            embedding_model = args.embedding_model or config.services.embedding_model
            rerank_endpoint = args.rerank_endpoint or config.services.rerank_endpoint
            chat_endpoint = args.chat_endpoint or config.services.chat_endpoint
            chat_model = args.chat_model or config.services.chat_model
            validate_step15_agent_config(
                qdrant_path=qdrant_path,
                qdrant_url=config.qdrant.url,
                collection_name=collection_name,
                embedding_endpoint=embedding_endpoint,
                embedding_model=embedding_model,
                rerank_endpoint=rerank_endpoint,
                chat_endpoint=chat_endpoint,
                chat_model=chat_model,
            )
            items, form_input_snapshot = prepare_step15_form_input(args, config, target_namespace, global_namespace)
        except (RuntimeError, ValueError) as exc:
            if getattr(exc, "report", None) is not None and not args.resume:
                write_json(args.out_dir / "form_parse_report.json", exc.report)
            parser.error(str(exc))
        api_key_env = args.deepseek_api_key_env or args.chat_api_key_env or config.services.chat_api_key_env
        retrieval_plan = resolve_step15_retrieval_plan(args.retrieval_plan, config)
        prompt_version = args.prompt_version or (
            config.agentic_mas.prompt_version
            if config.agentscope.mode == "agentic_mas"
            else "step15_compat"
        )
        runner = Step15AgentRunner(
            config=config,
            target_namespace=target_namespace,
            global_namespace=global_namespace,
            room_context=args.room_context,
            out_dir=args.out_dir,
            retrieval_plan=retrieval_plan,
            vector_top_k=args.vector_top_k or config.retrieval.vector_top_k,
            rerank_top_n=args.rerank_top_n or config.retrieval.rerank_top_n,
            judge_enabled=bool(args.judge),
            writeback_enabled=bool(args.writeback),
            overwrite_all_cli=args.existing_value_policy == "overwrite_all",
            template_path=args.template,
            checkpoint_every=args.checkpoint_every,
            resume=args.resume,
            form_input_snapshot=form_input_snapshot,
            timeout_seconds=args.timeout or config.services.timeout_seconds,
            chat_max_retries=args.chat_max_retries,
            chat_retry_backoff_seconds=args.chat_retry_backoff_seconds,
            prompt_version=prompt_version,
            judge_cache_path=args.judge_cache or (config.paths.artifacts_dir / "cache" / "judge_cache.jsonl"),
            use_judge_cache=bool(args.use_judge_cache),
            deepseek_api_key_env=api_key_env,
            qdrant_path=qdrant_path,
            collection_name=collection_name,
            index_scopes=args.normalized_index_scopes,
            embedding_endpoint=embedding_endpoint,
            embedding_model=embedding_model,
            rerank_endpoint=rerank_endpoint,
            rerank_model=args.rerank_model if args.rerank_model is not None else config.services.rerank_model,
            chat_endpoint=chat_endpoint,
            chat_model=chat_model,
            chat_api_key=args.deepseek_api_key if args.deepseek_api_key is not None else os.environ.get(api_key_env, ""),
            allowed_layers=config.retrieval.query_layers,
            layered_plan=config.retrieval.layered_plan,
            grounding_enabled=args.grounding_enabled,
            field_binding_enabled=args.field_binding_enabled,
            parent_payload_enabled=args.parent_payload_enabled,
        )
        predictions = runner.run(items)
        print(
            json.dumps(
                {
                    "field_count": len(predictions),
                    "out_dir": str(args.out_dir),
                    "run_id": runner.run_id,
                    "judge": bool(args.judge),
                    "writeback": runner.writeback_status,
                    "retrieval_plan": retrieval_plan,
                    "agentscope_mode": runner.mas_mode,
                },
                ensure_ascii=False,
            )
        )


def build_agent_retriever(
    args: argparse.Namespace,
    config,
    retrieval_backend: str,
    retrieval_plan: str,
    target_namespace: str,
    enable_rerank: bool,
    vector_top_k: int,
    rerank_top_n: int,
):
    del target_namespace
    if retrieval_backend == "mini":
        if not args.corpus:
            raise RuntimeError("retrieval-backend=mini requires --corpus")
        return MiniCorpusRetriever(load_corpus(args.corpus))
    qdrant_path = args.qdrant_path or config.paths.qdrant_path
    collection_name = args.qdrant_collection or config.qdrant.collection_name
    embedding_endpoint = args.embedding_endpoint or config.services.embedding_endpoint
    embedding_model = args.embedding_model or config.services.embedding_model
    missing = [
        name
        for name, value in [
            ("qdrant_path", qdrant_path),
            ("collection_name", collection_name),
            ("embedding_endpoint", embedding_endpoint),
            ("embedding_model", embedding_model),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError("retrieval-backend=qdrant requires qdrant_path or qdrant.url, collection_name, embedding_endpoint, embedding_model")
    qdrant_retriever = QdrantRetriever(
        qdrant_path=qdrant_path,
        qdrant_url=config.qdrant.url,
        qdrant_api_key_env=config.qdrant.api_key_env,
        collection_name=collection_name,
        embedding_endpoint=embedding_endpoint,
        embedding_model=embedding_model,
        prefer_grpc=config.qdrant.prefer_grpc,
        timeout=config.qdrant.timeout,
    )
    rerank_client = None
    if enable_rerank:
        rerank_endpoint = args.rerank_endpoint or config.services.rerank_endpoint
        if not rerank_endpoint:
            raise RuntimeError("enable-rerank requires rerank_endpoint")
        rerank_client = RerankClient(
            endpoint=rerank_endpoint,
            model=args.rerank_model or config.services.rerank_model,
            timeout_seconds=config.services.timeout_seconds,
        )
    if retrieval_plan == "layered":
        return LayeredQdrantEvidenceRetriever(
            qdrant_retriever=qdrant_retriever,
            layered_plan=config.retrieval.layered_plan,
            global_namespace=config.retrieval.global_namespace,
            enable_rerank=enable_rerank,
            rerank_client=rerank_client,
            vector_top_k=config.retrieval.layer_top_k or vector_top_k,
            rerank_top_n=config.retrieval.layer_rerank_top_n or rerank_top_n,
            max_reference_chunks=config.retrieval.max_reference_chunks,
        )
    return QdrantEvidenceRetriever(
        qdrant_retriever=qdrant_retriever,
        enable_rerank=enable_rerank,
        rerank_client=rerank_client,
        rerank_top_n=rerank_top_n,
        vector_top_k=vector_top_k,
        query_layers=config.retrieval.query_layers,
    )


def step15_agentic_cli_overrides(args: argparse.Namespace) -> dict[str, object]:
    overrides: dict[str, object] = {}
    if getattr(args, "sufficiency_enabled", None) is not None:
        overrides["retrieval"] = {"sufficiency_enabled": args.sufficiency_enabled}
    if getattr(args, "schema_first_enabled", None) is not None:
        overrides.setdefault("retrieval", {})["schema_first_enabled"] = args.schema_first_enabled
    if getattr(args, "agentic_mas", False):
        overrides["agentscope"] = {"enabled": True, "mode": "agentic_mas"}
        if getattr(args, "sufficiency_enabled", None) is None:
            overrides.setdefault("retrieval", {})["sufficiency_enabled"] = False
        overrides["agentic_mas"] = {"enabled": True}
    agentic_overrides: dict[str, object] = dict(overrides.get("agentic_mas") or {})
    if getattr(args, "agentic_max_rounds", None) is not None:
        agentic_overrides["max_rounds"] = args.agentic_max_rounds
    workflow_overrides: dict[str, object] = {}
    if getattr(args, "disable_missing_info", False):
        workflow_overrides["missing_info"] = {"enabled": False}
    if getattr(args, "disable_wrong_answer_risk", False):
        workflow_overrides["wrong_answer_risk"] = {"enabled": False}
    if getattr(args, "disable_not_found_recovery", False):
        workflow_overrides["not_found_recovery"] = {"enabled": False}
    if getattr(args, "disable_uncertainty_conflict", False):
        workflow_overrides["uncertainty_conflict"] = {"enabled": False}
    if workflow_overrides:
        agentic_overrides["workflows"] = workflow_overrides
    if agentic_overrides:
        overrides["agentic_mas"] = agentic_overrides
    return overrides


def build_agent_generator(args: argparse.Namespace, config, generation_backend: str):
    if generation_backend == "deterministic":
        return DeterministicAnswerGenerator()
    chat_endpoint = args.chat_endpoint or config.services.chat_endpoint
    chat_model = args.chat_model or config.services.chat_model
    if not chat_endpoint or not chat_model:
        raise RuntimeError("generation-backend=llm requires chat_endpoint and chat_model")
    api_key_env = args.chat_api_key_env or config.services.chat_api_key_env
    return LLMAnswerGenerator(
        chat_endpoint=chat_endpoint,
        chat_model=chat_model,
        api_key=os.environ.get(api_key_env, ""),
        timeout_seconds=config.services.timeout_seconds,
    )


def resolve_step15_retrieval_plan(retrieval_plan: str | None, config) -> str:
    if retrieval_plan not in {None, "layered"}:
        raise ValueError("Step15 production retrieval_plan must be layered")
    configured = getattr(config.retrieval, "plan", "layered")
    if configured != "layered":
        raise ValueError("Step15 production retrieval_plan must be layered")
    return "layered"


if __name__ == "__main__":
    main()
