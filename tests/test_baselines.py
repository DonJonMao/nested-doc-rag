from pathlib import Path

from nested_doc_rag.evaluation.baselines import build_summary


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
        retrieval_mode="flat",
        layered_plan=[],
    )

    assert summary["sample_count"] == 2
    assert summary["label_counts"] == {"exact": 1, "partial": 1}
    assert summary["acceptable_or_better"] == 1
    assert summary["partial_or_better"] == 2
