from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nested_doc_rag.config import discover_project_root, load_yaml_file
from nested_doc_rag.evaluation.field_metrics import evaluate_fields
from nested_doc_rag.io import read_jsonl, write_json, write_jsonl
from nested_doc_rag.schemas.eval import FieldGold, FieldMetricRow, FieldPrediction

BASELINE_METHODS = [
    "naive_fixed_chunk_vector",
    "flat_vector_rag",
    "layered_rag",
    "layered_rag_with_validation",
    "agentic_repair_rag",
]


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    text: str
    namespace: str = "global"
    corpus_layer: str = "fact"
    source_type: str = "mini_fact"
    field_id: str | None = None
    question_text: str | None = None
    answer_value: Any = None
    answer_status: str = "answered"
    source_chunk_ids: list[str] = field(default_factory=list)
    evidence_attachment_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> KnowledgeChunk:
        return cls(
            chunk_id=str(value["chunk_id"]),
            text=str(value.get("text") or ""),
            namespace=str(value.get("namespace") or "global"),
            corpus_layer=str(value.get("corpus_layer") or "fact"),
            source_type=str(value.get("source_type") or "mini_fact"),
            field_id=str(value["field_id"]) if value.get("field_id") is not None else None,
            question_text=str(value.get("question_text") or ""),
            answer_value=value.get("answer_value"),
            answer_status=str(value.get("answer_status") or "answered"),
            source_chunk_ids=[str(item) for item in value.get("source_chunk_ids") or []],
            evidence_attachment_ids=[str(item) for item in value.get("evidence_attachment_ids") or []],
        )


@dataclass(frozen=True)
class ExperimentConfig:
    dataset: dict[str, Any]
    gold_file: Path
    corpus_file: Path
    target_namespace: str
    global_namespace: str = "global"
    retrieval_modes: list[str] = field(default_factory=lambda: list(BASELINE_METHODS))
    top_k: int = 3
    rerank_top_n: int = 2
    fixed_chunk_chars: int = 220
    output_dir: Path = Path("artifacts/experiments/baselines")
    resume: bool = True
    model_config: dict[str, Any] = field(default_factory=dict)
    layered_plan: list[dict[str, Any]] = field(default_factory=list)
    max_repair_attempts: int = 2


def load_experiment_config(config_path: Path, *, out_dir: Path | None = None) -> ExperimentConfig:
    project_root = discover_project_root()
    config_path = Path(config_path).expanduser()
    if not config_path.is_absolute():
        config_path = project_root / config_path
    raw = load_yaml_file(config_path)

    def resolve(value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = project_root / path
        return path.resolve()

    dataset = dict(raw.get("dataset") or {})
    gold_value = raw.get("gold_file") or raw.get("gold") or dataset.get("gold_file")
    corpus_value = raw.get("corpus_file") or dataset.get("corpus_file")
    if not gold_value:
        raise ValueError("experiment config missing gold_file")
    if not corpus_value:
        raise ValueError("experiment config missing corpus_file")

    return ExperimentConfig(
        dataset=dataset,
        gold_file=resolve(gold_value),
        corpus_file=resolve(corpus_value),
        target_namespace=str(raw.get("target_namespace") or "global"),
        global_namespace=str(raw.get("global_namespace") or "global"),
        retrieval_modes=[str(item) for item in raw.get("retrieval_modes") or BASELINE_METHODS],
        top_k=int(raw.get("top_k") or 3),
        rerank_top_n=int(raw.get("rerank_top_n") or 2),
        fixed_chunk_chars=int(raw.get("fixed_chunk_chars") or 220),
        output_dir=resolve(out_dir or raw.get("output_dir") or "artifacts/experiments/baselines"),
        resume=bool(raw.get("resume", True)),
        model_config=dict(raw.get("model_config") or {}),
        layered_plan=list(raw.get("layered_plan") or []),
        max_repair_attempts=int(raw.get("max_repair_attempts") or 2),
    )


def run_baseline_experiment(config_path: Path, *, out_dir: Path | None = None, resume: bool | None = None) -> dict[str, Any]:
    config = load_experiment_config(config_path, out_dir=out_dir)
    resume = config.resume if resume is None else resume
    config.output_dir.mkdir(parents=True, exist_ok=True)
    predictions_dir = config.output_dir / "predictions"
    metrics_dir = config.output_dir / "metrics"
    badcases_dir = config.output_dir / "badcases"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    badcases_dir.mkdir(parents=True, exist_ok=True)

    golds = [FieldGold.from_dict(record) for record in read_jsonl(config.gold_file)]
    chunks = [KnowledgeChunk.from_dict(record) for record in read_jsonl(config.corpus_file)]

    method_results: dict[str, dict[str, Any]] = {}
    predictions_by_method: dict[str, list[FieldPrediction]] = {}
    rows_by_method: dict[str, list[FieldMetricRow]] = {}
    for method_name in config.retrieval_modes:
        if method_name not in BASELINE_METHODS:
            raise ValueError(f"unsupported baseline method: {method_name}")
        prediction_path = predictions_dir / f"{method_name}.jsonl"
        if resume and prediction_path.exists():
            predictions = [FieldPrediction.from_dict(record) for record in read_jsonl(prediction_path)]
            predictions = with_method_name(predictions, method_name)
            if {prediction.field_id for prediction in predictions} != {gold.field_id for gold in golds}:
                predictions = run_single_baseline(method_name, golds, chunks, config)
                write_jsonl(prediction_path, [prediction.to_dict() for prediction in predictions])
        else:
            predictions = run_single_baseline(method_name, golds, chunks, config)
            write_jsonl(prediction_path, [prediction.to_dict() for prediction in predictions])
        evaluation = evaluate_fields(golds, predictions)
        predictions_by_method[method_name] = predictions
        rows_by_method[method_name] = evaluation.rows
        method_results[method_name] = {
            "method": method_name,
            "field_accuracy": evaluation.metrics["field_semantic_match"],
            "evidence_support_rate": evaluation.metrics["evidence_support_rate"],
            "status_accuracy": evaluation.metrics["answer_status_accuracy"],
            "constraint_violation_rate": evaluation.metrics["constraint_violation_rate"],
            "human_review_rate": evaluation.metrics["human_review_rate"],
            "p95_latency": p95([float(pred.validation.get("latency_ms") or 0.0) for pred in predictions]),
            "avg_cost": mean([float(pred.validation.get("cost") or 0.0) for pred in predictions]),
        }

    summary = {
        "dataset": config.dataset,
        "gold_file": str(config.gold_file),
        "corpus_file": str(config.corpus_file),
        "target_namespace": config.target_namespace,
        "global_namespace": config.global_namespace,
        "retrieval_modes": config.retrieval_modes,
        "top_k": config.top_k,
        "rerank_top_n": config.rerank_top_n,
        "model_config": config.model_config,
        "output_dir": str(config.output_dir),
        "methods": [method_results[name] for name in config.retrieval_modes],
    }
    write_json(metrics_dir / "summary.json", summary)
    write_summary_csv(metrics_dir / "summary.csv", summary["methods"])
    (metrics_dir / "summary.md").write_text(render_summary_markdown(summary["methods"]), encoding="utf-8")

    write_jsonl(
        badcases_dir / "naive_vs_layered.jsonl",
        diff_method_rows("naive_fixed_chunk_vector", "layered_rag", rows_by_method),
    )
    write_jsonl(
        badcases_dir / "layered_vs_agentic_repair.jsonl",
        diff_method_rows("layered_rag", "agentic_repair_rag", rows_by_method),
    )
    return summary


def with_method_name(predictions: list[FieldPrediction], method_name: str) -> list[FieldPrediction]:
    output: list[FieldPrediction] = []
    for prediction in predictions:
        if prediction.method_name == method_name:
            output.append(prediction)
            continue
        output.append(
            FieldPrediction(
                field_id=prediction.field_id,
                row_index=prediction.row_index,
                target_cell=prediction.target_cell,
                answer_value=prediction.answer_value,
                answer_status=prediction.answer_status,
                confidence=prediction.confidence,
                source_chunk_ids=prediction.source_chunk_ids,
                evidence_attachment_ids=prediction.evidence_attachment_ids,
                validation=prediction.validation,
                method_name=method_name,
            )
        )
    return output


def run_single_baseline(
    method_name: str,
    golds: list[FieldGold],
    chunks: list[KnowledgeChunk],
    config: ExperimentConfig,
) -> list[FieldPrediction]:
    predictions: list[FieldPrediction] = []
    for gold in golds:
        started = time.perf_counter()
        candidates = retrieve_candidates(method_name, gold, chunks, config)
        prediction = generate_prediction(method_name, gold, candidates, config)
        latency_ms = method_latency_ms(method_name) + (time.perf_counter() - started) * 1000
        validation = dict(prediction.validation)
        validation.update(
            {
                "latency_ms": round(latency_ms, 3),
                "cost": method_cost(method_name),
                "retrieved_chunk_ids": [chunk.chunk_id for chunk in candidates],
            }
        )
        predictions.append(
            FieldPrediction(
                field_id=prediction.field_id,
                row_index=prediction.row_index,
                target_cell=prediction.target_cell,
                answer_value=prediction.answer_value,
                answer_status=prediction.answer_status,
                confidence=prediction.confidence,
                source_chunk_ids=prediction.source_chunk_ids,
                evidence_attachment_ids=prediction.evidence_attachment_ids,
                validation=validation,
                method_name=method_name,
            )
        )
    return predictions


def retrieve_candidates(
    method_name: str,
    gold: FieldGold,
    chunks: list[KnowledgeChunk],
    config: ExperimentConfig,
) -> list[KnowledgeChunk]:
    if method_name == "naive_fixed_chunk_vector":
        candidates = fixed_chunk_candidates(chunks, config.fixed_chunk_chars)
        scored = [(lexical_score(gold.question_text, chunk.text), index, chunk) for index, chunk in enumerate(candidates)]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [chunk for score, _index, chunk in scored if score > 0][: config.top_k]

    namespace_allowed = {config.target_namespace, config.global_namespace}
    filtered = [chunk for chunk in chunks if chunk.namespace in namespace_allowed]
    if method_name == "flat_vector_rag":
        scored = [
            (
                lexical_score(gold.question_text, chunk.text) + (2.0 if chunk.namespace == config.target_namespace else 0.0),
                index,
                chunk,
            )
            for index, chunk in enumerate(filtered)
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [chunk for score, _index, chunk in scored if score > 0][: config.top_k]

    return layered_candidates(gold, filtered, config)[: config.top_k]


def fixed_chunk_candidates(chunks: list[KnowledgeChunk], chunk_chars: int) -> list[KnowledgeChunk]:
    output: list[KnowledgeChunk] = []
    for chunk in chunks:
        text = chunk.text
        for index, start in enumerate(range(0, max(len(text), 1), chunk_chars)):
            output.append(
                KnowledgeChunk(
                    chunk_id=f"{chunk.chunk_id}_fixed_{index}",
                    text=text[start : start + chunk_chars],
                    namespace=chunk.namespace,
                    corpus_layer="fixed_chunk",
                    source_type="fixed_chunk",
                    field_id=chunk.field_id,
                    question_text=chunk.question_text,
                    answer_value=chunk.answer_value,
                    answer_status=chunk.answer_status,
                    source_chunk_ids=[],
                    evidence_attachment_ids=[],
                )
            )
    return output


def layered_candidates(gold: FieldGold, chunks: list[KnowledgeChunk], config: ExperimentConfig) -> list[KnowledgeChunk]:
    plan = config.layered_plan or [
        {"namespaces": "target", "corpus_layers": ["fact", "evidence"], "source_types": ["main_excel_capability"]},
        {"namespaces": "target", "corpus_layers": ["fact"], "source_types": ["embedded_word_table", "embedded_raw_segment"]},
        {"namespaces": "global", "corpus_layers": ["fact", "intro_doc"], "source_types": ["intro_doc_paragraph", "mini_fact"]},
    ]
    output: list[KnowledgeChunk] = []
    seen: set[str] = set()
    for layer in plan:
        namespace = config.target_namespace if layer.get("namespaces") == "target" else config.global_namespace
        allowed_layers = set(layer.get("corpus_layers") or [])
        allowed_types = set(layer.get("source_types") or [])
        layer_chunks = [
            chunk
            for chunk in chunks
            if chunk.namespace == namespace
            and (not allowed_layers or chunk.corpus_layer in allowed_layers)
            and (not allowed_types or chunk.source_type in allowed_types)
        ]
        scored = [(lexical_score(gold.question_text, chunk.text), index, chunk) for index, chunk in enumerate(layer_chunks)]
        scored.sort(key=lambda item: (-item[0], item[1]))
        for score, _index, chunk in scored:
            if score <= 0 or chunk.chunk_id in seen:
                continue
            output.append(chunk)
            seen.add(chunk.chunk_id)
    return output


def generate_prediction(
    method_name: str,
    gold: FieldGold,
    candidates: list[KnowledgeChunk],
    config: ExperimentConfig,
) -> FieldPrediction:
    base = prediction_from_chunk(method_name, gold, candidates[0] if candidates else None, confidence=0.72 if candidates else 0.2)
    if method_name == "layered_rag_with_validation":
        return apply_validation(method_name, gold, base)
    if method_name == "agentic_repair_rag":
        validated = apply_validation(method_name, gold, base)
        if validated.answer_status == "answered":
            return validated
        for chunk in candidates[: config.max_repair_attempts + 1]:
            repaired = apply_validation(method_name, gold, prediction_from_chunk(method_name, gold, chunk, confidence=0.82))
            if repaired.answer_status == "answered":
                return FieldPrediction(
                    field_id=repaired.field_id,
                    row_index=repaired.row_index,
                    target_cell=repaired.target_cell,
                    answer_value=repaired.answer_value,
                    answer_status=repaired.answer_status,
                    confidence=repaired.confidence,
                    source_chunk_ids=repaired.source_chunk_ids,
                    evidence_attachment_ids=repaired.evidence_attachment_ids,
                    validation={**repaired.validation, "repair_attempted": True},
                    method_name=method_name,
                )
        return validated
    return base


def prediction_from_chunk(method_name: str, gold: FieldGold, chunk: KnowledgeChunk | None, *, confidence: float) -> FieldPrediction:
    if not chunk:
        return FieldPrediction(
            field_id=gold.field_id,
            row_index=gold.row_index,
            target_cell=gold.target_cell,
            answer_value="未找到",
            answer_status="not_found",
            confidence=0.1,
            method_name=method_name,
        )
    return FieldPrediction(
        field_id=gold.field_id,
        row_index=gold.row_index,
        target_cell=gold.target_cell,
        answer_value=chunk.answer_value,
        answer_status=chunk.answer_status,
        confidence=confidence,
        source_chunk_ids=chunk.source_chunk_ids,
        evidence_attachment_ids=chunk.evidence_attachment_ids,
        validation={"selected_chunk_id": chunk.chunk_id},
        method_name=method_name,
    )


def apply_validation(method_name: str, gold: FieldGold, prediction: FieldPrediction) -> FieldPrediction:
    from nested_doc_rag.evaluation.field_metrics import validate_constraints

    violations = validate_constraints(gold, prediction)
    evidence_missing = gold.must_have_evidence and prediction.answer_status == "answered" and not prediction.source_chunk_ids
    if not violations and not evidence_missing:
        return prediction
    return FieldPrediction(
        field_id=prediction.field_id,
        row_index=prediction.row_index,
        target_cell=prediction.target_cell,
        answer_value="未找到",
        answer_status="partial_clue" if prediction.answer_value not in {"", "未找到", None} else "not_found",
        confidence=min(prediction.confidence, 0.5),
        source_chunk_ids=[],
        evidence_attachment_ids=[],
        validation={**prediction.validation, "validation_failed": True, "constraint_violations": violations, "evidence_missing": evidence_missing},
        method_name=method_name,
    )


def lexical_score(query: str, text: str) -> float:
    query_tokens = tokenize(query)
    text_value = text.casefold()
    if not query_tokens:
        return 0.0
    return sum(1 for token in query_tokens if token in text_value)


def tokenize(text: str) -> list[str]:
    raw = str(text or "").casefold()
    tokens = re_split_tokens(raw)
    phrase_tokens = [raw.strip()] if raw.strip() else []
    return [token for token in [*phrase_tokens, *tokens] if len(token) >= 2]


def re_split_tokens(text: str) -> list[str]:
    import re

    return [item for item in re.split(r"[\s,，;；:：。.!！?？/\\()（）【】\[\]{}<>《》\"'“”‘’`~_-]+", text) if item]


def diff_method_rows(base_method: str, improved_method: str, rows_by_method: dict[str, list[FieldMetricRow]]) -> list[dict[str, Any]]:
    base_by_id = {row.field_id: row for row in rows_by_method.get(base_method, [])}
    improved_by_id = {row.field_id: row for row in rows_by_method.get(improved_method, [])}
    diffs: list[dict[str, Any]] = []
    for field_id, base_row in base_by_id.items():
        improved_row = improved_by_id.get(field_id)
        if not improved_row:
            continue
        improvements = improvement_reasons(base_row, improved_row)
        if not improvements:
            continue
        diffs.append(
            {
                "field_id": field_id,
                "row_index": base_row.row_index,
                "target_cell": base_row.target_cell,
                "base_method": base_method,
                "improved_method": improved_method,
                "improvements": improvements,
                "base": base_row.to_dict(),
                "improved": improved_row.to_dict(),
            }
        )
    return diffs


def improvement_reasons(base: FieldMetricRow, improved: FieldMetricRow) -> list[str]:
    reasons: list[str] = []
    if not base.semantic_match and improved.semantic_match:
        reasons.append("semantic_match_improved")
    if not base.status_match and improved.status_match:
        reasons.append("status_match_improved")
    if not base.evidence_supported and improved.evidence_supported:
        reasons.append("evidence_support_improved")
    if base.constraint_violations and not improved.constraint_violations:
        reasons.append("constraint_violation_fixed")
    if base.needs_human_review and not improved.needs_human_review:
        reasons.append("human_review_avoided")
    if base.correction_required and not improved.correction_required:
        reasons.append("correction_avoided")
    return reasons


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "method",
        "field_accuracy",
        "evidence_support_rate",
        "status_accuracy",
        "constraint_violation_rate",
        "human_review_rate",
        "p95_latency",
        "avg_cost",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def render_summary_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Baseline 对比实验",
        "",
        "| method | field_accuracy | evidence_support_rate | status_accuracy | constraint_violation_rate | human_review_rate | p95_latency | avg_cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['method']} | {row['field_accuracy']:.4f} | {row['evidence_support_rate']:.4f} | "
            f"{row['status_accuracy']:.4f} | {row['constraint_violation_rate']:.4f} | {row['human_review_rate']:.4f} | "
            f"{row['p95_latency']:.3f} | {row['avg_cost']:.6f} |"
        )
    return "\n".join(lines) + "\n"


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * 0.95)))
    return round(sorted_values[index], 6)


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def method_latency_ms(method_name: str) -> float:
    return {
        "naive_fixed_chunk_vector": 12.0,
        "flat_vector_rag": 20.0,
        "layered_rag": 32.0,
        "layered_rag_with_validation": 36.0,
        "agentic_repair_rag": 55.0,
    }[method_name]


def method_cost(method_name: str) -> float:
    return {
        "naive_fixed_chunk_vector": 0.001,
        "flat_vector_rag": 0.002,
        "layered_rag": 0.003,
        "layered_rag_with_validation": 0.003,
        "agentic_repair_rag": 0.005,
    }[method_name]
