from pathlib import Path

from nested_doc_rag.evaluation.baselines import build_summary
from nested_doc_rag.evaluation.experiment_runner import BASELINE_METHODS, run_baseline_experiment
from nested_doc_rag.io import read_jsonl


def test_build_summary_counts_labels() -> None:
    summary = build_summary(
        [
            {"row_index": 4, "judge": {"label": "exact", "score": 1}, "generated_answer": {"answer_status": "answered"}},
            {"row_index": 5, "judge": {"label": "partial", "score": 0.5}, "generated_answer": {"answer_status": "partial_clue"}},
        ],
        rows=[4, 5],
        collection_name="demo",
        qdrant_path=Path("/tmp/qdrant"),
        target_namespace="xixian_4",
        global_namespace="global",
        layers=["fact"],
        room_context="西咸4号楼 301机房",
        retrieval_mode="layered",
        layered_plan=[],
    )

    assert summary["sample_count"] == 2
    assert summary["label_counts"] == {"exact": 1, "partial": 1}
    assert summary["acceptable_or_better"] == 1
    assert summary["partial_or_better"] == 2


def test_run_baseline_experiment_outputs_unified_predictions(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    summary = run_baseline_experiment(
        repo_root / "experiments/form_filling_baselines.yaml",
        out_dir=tmp_path / "baselines",
        resume=False,
    )

    assert [item["method"] for item in summary["methods"]] == BASELINE_METHODS
    for method_name in BASELINE_METHODS:
        prediction_path = tmp_path / "baselines" / "predictions" / f"{method_name}.jsonl"
        assert prediction_path.exists()
        records = read_jsonl(prediction_path)
        assert records
        assert {record["method_name"] for record in records} == {method_name}
        assert {record["field_id"] for record in records} == {"field_text", "field_enum", "field_number", "field_date", "field_bool"}

    summary_md = (tmp_path / "baselines" / "metrics" / "summary.md").read_text(encoding="utf-8")
    assert (
        "| method | field_accuracy | evidence_support_rate | status_accuracy | "
        "constraint_violation_rate | human_review_rate | p95_latency | avg_cost |"
    ) in summary_md

    naive_diff = read_jsonl(tmp_path / "baselines" / "badcases" / "naive_vs_layered.jsonl")
    repair_diff = read_jsonl(tmp_path / "baselines" / "badcases" / "layered_vs_agentic_repair.jsonl")
    assert any("semantic_match_improved" in item["improvements"] for item in naive_diff)
    assert any(item["field_id"] == "field_bool" and "constraint_violation_fixed" in item["improvements"] for item in repair_diff)


def test_run_baseline_experiment_resume(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / "experiments/form_filling_baselines.yaml"
    out_dir = tmp_path / "baselines"

    run_baseline_experiment(config_path, out_dir=out_dir, resume=False)
    first_records = read_jsonl(out_dir / "predictions" / "layered_rag.jsonl")
    run_baseline_experiment(config_path, out_dir=out_dir, resume=True)
    second_records = read_jsonl(out_dir / "predictions" / "layered_rag.jsonl")

    assert second_records == first_records
