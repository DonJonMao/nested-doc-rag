from __future__ import annotations

import json
from pathlib import Path

from full_qdrant_index import DEFAULT_OUT_DIR, build_expanded_manifest, read_jsonl


def test_manifest_outputs_exist() -> None:
    if not (DEFAULT_OUT_DIR / "expanded_ingestion_manifest.jsonl").exists():
        build_expanded_manifest(DEFAULT_OUT_DIR)
    assert (DEFAULT_OUT_DIR / "expanded_ingestion_manifest.jsonl").exists()
    assert (DEFAULT_OUT_DIR / "manifest_summary.json").exists()
    assert (DEFAULT_OUT_DIR / "visualization.md").exists()


def test_exclusion_policy() -> None:
    records = read_jsonl(DEFAULT_OUT_DIR / "expanded_ingestion_manifest.jsonl")
    assert records
    for record in records:
        assert "基地云机房信息调研表" not in str(record.get("file_name"))
        assert "工勘单" not in str(record.get("relative_path"))
        assert str(record.get("embedded_file_type") or "").lower() != "dwg"
        assert "image" not in str(record.get("source_type")).lower()


def test_intro_doc_is_included() -> None:
    records = read_jsonl(DEFAULT_OUT_DIR / "expanded_ingestion_manifest.jsonl")
    intro = [record for record in records if record.get("source_type", "").startswith("intro_doc")]
    assert intro
    assert any("4号楼301机房" in str(record.get("raw_text")) for record in intro)


def test_raw_embedded_text_is_included() -> None:
    records = read_jsonl(DEFAULT_OUT_DIR / "expanded_ingestion_manifest.jsonl")
    assert any(record.get("source_type") == "embedded_raw_segment" for record in records)
    assert any(record.get("embedded_file_type") == "pdf" for record in records)
    assert any(record.get("embedded_file_type") == "pptx" for record in records)


def test_summary_counts_match_manifest() -> None:
    records = read_jsonl(DEFAULT_OUT_DIR / "expanded_ingestion_manifest.jsonl")
    summary = json.loads((DEFAULT_OUT_DIR / "manifest_summary.json").read_text(encoding="utf-8"))
    assert summary["total_records"] == len(records)
    assert summary["counts_by_full_store_source"]["step11_curated_manifest"] == 2835
    assert summary["counts_by_full_store_source"]["step04b_raw_embedded_segments"] > 6000
    assert summary["counts_by_full_store_source"]["step04a_intro_doc"] > 300


def test_qdrant_build_summary_if_present() -> None:
    summary_path = DEFAULT_OUT_DIR / "qdrant_build_summary.json"
    if not summary_path.exists():
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["collection_name"] == "datacenter_chunks_v1"
    assert summary["qdrant_points_count"] == summary["total_manifest_records"]
    assert summary["qdrant_points_count"] == 9533
    assert summary["dimension"] == 4096


if __name__ == "__main__":
    test_manifest_outputs_exist()
    test_exclusion_policy()
    test_intro_doc_is_included()
    test_raw_embedded_text_is_included()
    test_summary_counts_match_manifest()
    test_qdrant_build_summary_if_present()
    print("step 15 full qdrant manifest tests passed")
