from __future__ import annotations

import json
from pathlib import Path

from embedding_pipeline import DEFAULT_OUT_DIR, build_manifest, read_jsonl, select_records


def test_manifest_counts() -> None:
    summary = build_manifest(DEFAULT_OUT_DIR)
    assert summary["total_records"] == 2835
    assert summary["default_index_records"] == 2624
    assert summary["counts_by_source_type"]["main_excel_capability"] == 1612
    assert summary["counts_by_source_type"]["embedded_word_table"] == 1223
    assert summary["counts_by_embedding_policy"]["exclude"] == 165
    assert summary["missing_step10_audit_records"] == 0


def test_manifest_records_have_required_fields() -> None:
    records = read_jsonl(DEFAULT_OUT_DIR / "ingestion_manifest.jsonl")
    required = {
        "chunk_id",
        "source_type",
        "namespace",
        "corpus_layer",
        "embedding_policy",
        "default_index",
        "text_for_embedding",
        "raw_text",
        "source",
    }
    for record in records[:100]:
        assert required.issubset(record)
        assert record["chunk_id"].startswith("rag_")
        assert record["namespace"]
        assert record["text_for_embedding"]


def test_sample_selection_is_query_biased() -> None:
    records = read_jsonl(DEFAULT_OUT_DIR / "ingestion_manifest.jsonl")
    sample = select_records(records, limit=80)
    joined = "\n".join(record["text_for_embedding"] for record in sample)
    assert len(sample) == 80
    assert "停电" in joined or "油机" in joined
    assert "零信任" in joined or "4A" in joined
    assert "故障排查" in joined or "路由故障" in joined
    assert "温湿度" in joined or "冷通道" in joined


def test_index_outputs_if_built() -> None:
    meta_path = DEFAULT_OUT_DIR / "index_meta.json"
    if not meta_path.exists():
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    records = read_jsonl(Path(meta["index_records_path"]))
    embeddings_path = Path(meta["embeddings_path"])
    assert meta["record_count"] == len(records)
    assert meta["dimension"] > 0
    assert embeddings_path.exists()
    assert embeddings_path.stat().st_size == meta["record_count"] * meta["dimension"] * 4


def test_retrieval_report_if_built() -> None:
    report_path = DEFAULT_OUT_DIR / "retrieval_smoke.json"
    if not report_path.exists():
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["query_count"] == 4
    for result in report["results"]:
        assert result["reranked_hits"]


if __name__ == "__main__":
    test_manifest_counts()
    test_manifest_records_have_required_fields()
    test_sample_selection_is_query_biased()
    test_index_outputs_if_built()
    test_retrieval_report_if_built()
    print("step 11 embedding pipeline tests passed")
