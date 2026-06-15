from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
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
from nested_doc_rag.gongkan_eval import select_eval_items
from nested_doc_rag.retrieval import QdrantRetriever

from .agent.runner import FieldFillingAgent, load_corpus, load_fields
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

    artifacts_parser = subparsers.add_parser("validate-artifacts", help="Validate a frozen Step15AgentRunner artifact directory.")
    artifacts_parser.add_argument("--run-dir", type=Path, required=True, help="Step15AgentRunner output directory.")
    artifacts_parser.add_argument(
        "--allow-mutated-predictions",
        action="store_true",
        help="Allow predictions.jsonl to differ from predictions_raw.jsonl. Disabled for overlay mode.",
    )

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
    agent_parser.add_argument("--retrieval-plan", choices=["flat", "layered"], default=None, help="Qdrant retrieval plan.")
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
    step15_agent_parser.add_argument("--retrieval-mode", choices=["flat", "layered"], default=None, help="Step 15 retrieval mode.")
    step15_agent_parser.add_argument(
        "--prompt-version",
        choices=["step15_compat", "agent_v2"],
        default="step15_compat",
        help="Answer prompt version. step15_compat preserves the Step 15 effect prompt.",
    )
    step15_agent_parser.add_argument("--vector-top-k", type=int, default=None, help="Flat vector retrieval top-k.")
    step15_agent_parser.add_argument("--rerank-top-n", type=int, default=None, help="Flat rerank top-n.")
    judge_group = step15_agent_parser.add_mutually_exclusive_group()
    judge_group.add_argument("--judge", dest="judge", action="store_true", default=False, help="Run heldout-answer judge.")
    judge_group.add_argument("--no-judge", dest="judge", action="store_false", help="Disable judge. This is production mode.")
    step15_agent_parser.add_argument("--resume", action="store_true", help="Resume from field-level checkpoints in out-dir.")
    step15_agent_parser.add_argument("--checkpoint-every", type=int, default=1, help="Write checkpoint every N fields.")
    step15_agent_parser.add_argument("--template", type=Path, default=None, help="Optional Excel template for safe writeback.")
    step15_agent_parser.add_argument("--writeback", action="store_true", help="Enable safe Excel writeback.")
    step15_agent_parser.add_argument("--out-dir", type=Path, required=True, help="Run output directory.")
    step15_agent_parser.add_argument("--qdrant-path", type=Path, default=None, help="Qdrant local path.")
    step15_agent_parser.add_argument("--qdrant-collection", default=None, help="Qdrant collection name.")
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
    step15_agent_parser.add_argument("--chat-max-retries", type=int, default=2, help="Maximum chat timeout retries.")
    step15_agent_parser.add_argument("--chat-retry-backoff-seconds", type=int, default=3, help="Seconds to wait between chat retries.")
    step15_agent_parser.add_argument("--judge-cache", type=Path, default=None, help="Judge cache JSONL path.")
    judge_cache_group = step15_agent_parser.add_mutually_exclusive_group()
    judge_cache_group.add_argument("--use-judge-cache", dest="use_judge_cache", action="store_true", default=False, help="Reuse cached judge results.")
    judge_cache_group.add_argument("--no-judge-cache", dest="use_judge_cache", action="store_false", help="Disable judge cache.")
    step15_agent_parser.add_argument(
        "--mas-mode",
        choices=["off", "equivalent_mas", "enhanced_mas", "trace_only"],
        default="off",
        help="Optional Step15 MAS mode. Default off preserves the original production path.",
    )
    agentscope_group = step15_agent_parser.add_mutually_exclusive_group()
    agentscope_group.add_argument("--agentscope-enabled", dest="agentscope_enabled", action="store_true", default=False)
    agentscope_group.add_argument("--no-agentscope-enabled", dest="agentscope_enabled", action="store_false")
    step15_agent_parser.add_argument("--max-supplemental-rounds", type=int, default=1, help="Maximum enhanced_mas supplemental retrieval rounds.")
    step15_agent_parser.add_argument(
        "--semantic-risk-critic-enabled",
        action="store_true",
        default=False,
        help="Enable optional semantic risk suggestions in enhanced_mas.",
    )
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
    elif args.command == "validate-artifacts":
        try:
            result = validate_step15_artifacts(args.run_dir, allow_mutated_predictions=bool(args.allow_mutated_predictions))
        except ArtifactValidationError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
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
        retrieval_plan = args.retrieval_plan or (config.retrieval.retrieval_plan if retrieval_backend == "qdrant" else "flat")
        target_namespace = args.target_namespace or config.retrieval.target_namespace
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
        config = load_app_config(args.config, cli_overrides=step15_mas_cli_overrides(args))
        step12_dir = config.paths.artifacts_dir / "12_gongkan_form_analysis"
        form_items_path = args.form_items or (step12_dir / "form_items.jsonl")
        try:
            rows = parse_rows_arg(args.rows, step12_dir=step12_dir)
            target_namespace = args.target_namespace or config.retrieval.target_namespace
            global_namespace = args.global_namespace or config.retrieval.global_namespace
            qdrant_path = args.qdrant_path or config.paths.qdrant_path
            collection_name = args.qdrant_collection or config.qdrant.collection_name
            embedding_endpoint = args.embedding_endpoint or config.services.embedding_endpoint
            embedding_model = args.embedding_model or config.services.embedding_model
            rerank_endpoint = args.rerank_endpoint or config.services.rerank_endpoint
            chat_endpoint = args.chat_endpoint or config.services.chat_endpoint
            chat_model = args.chat_model or config.services.chat_model
            validate_step15_agent_config(
                qdrant_path=qdrant_path,
                collection_name=collection_name,
                embedding_endpoint=embedding_endpoint,
                embedding_model=embedding_model,
                rerank_endpoint=rerank_endpoint,
                chat_endpoint=chat_endpoint,
                chat_model=chat_model,
            )
            items = select_eval_items(rows, form_items_path=form_items_path)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        api_key_env = args.deepseek_api_key_env or args.chat_api_key_env or config.services.chat_api_key_env
        runner = Step15AgentRunner(
            config=config,
            target_namespace=target_namespace,
            global_namespace=global_namespace,
            room_context=args.room_context,
            out_dir=args.out_dir,
            retrieval_mode=args.retrieval_mode or "layered",
            vector_top_k=args.vector_top_k or config.retrieval.vector_top_k,
            rerank_top_n=args.rerank_top_n or config.retrieval.rerank_top_n,
            judge_enabled=bool(args.judge),
            writeback_enabled=bool(args.writeback),
            template_path=args.template,
            checkpoint_every=args.checkpoint_every,
            resume=args.resume,
            timeout_seconds=args.timeout or config.services.timeout_seconds,
            chat_max_retries=args.chat_max_retries,
            chat_retry_backoff_seconds=args.chat_retry_backoff_seconds,
            prompt_version=args.prompt_version,
            judge_cache_path=args.judge_cache or (config.paths.artifacts_dir / "cache" / "judge_cache.jsonl"),
            use_judge_cache=bool(args.use_judge_cache),
            deepseek_api_key_env=api_key_env,
            qdrant_path=qdrant_path,
            collection_name=collection_name,
            embedding_endpoint=embedding_endpoint,
            embedding_model=embedding_model,
            rerank_endpoint=rerank_endpoint,
            rerank_model=args.rerank_model if args.rerank_model is not None else config.services.rerank_model,
            chat_endpoint=chat_endpoint,
            chat_model=chat_model,
            chat_api_key=args.deepseek_api_key if args.deepseek_api_key is not None else os.environ.get(api_key_env, ""),
            allowed_layers=config.retrieval.query_layers,
            layered_plan=config.retrieval.layered_plan,
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
                },
                ensure_ascii=False,
            )
        )


def step15_mas_cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    mode = str(getattr(args, "mas_mode", "off") or "off")
    return {
        "agentscope": {
            "enabled": bool(getattr(args, "agentscope_enabled", False)),
            "mode": mode if mode in {"equivalent_mas", "enhanced_mas", "trace_only"} else "off",
            "optional_dependency": True,
        },
        "mas": {
            "enabled": mode != "off",
            "mode": mode,
            "max_supplemental_rounds": max(0, int(getattr(args, "max_supplemental_rounds", 1) or 0)),
            "semantic_risk_critic_enabled": bool(getattr(args, "semantic_risk_critic_enabled", False)),
        },
    }


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
        raise RuntimeError("retrieval-backend=qdrant requires qdrant_path, collection_name, embedding_endpoint, embedding_model")
    qdrant_retriever = QdrantRetriever(
        qdrant_path=qdrant_path,
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


if __name__ == "__main__":
    main()
