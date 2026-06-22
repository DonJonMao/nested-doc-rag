from __future__ import annotations

from pathlib import Path

import pytest

from nested_doc_rag.artifacts import ArtifactValidationError, validate_step15_artifacts
from nested_doc_rag.cli import build_parser, main
from nested_doc_rag.io import write_json, write_jsonl


def test_validate_artifacts_success(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path)

    result = validate_step15_artifacts(tmp_path)

    assert result["valid"] is True
    assert result["raw_rows"] == 1
    assert result["overlay_rows"] == 1


def test_validate_artifacts_requires_raw_compat_identity(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path)
    write_jsonl(tmp_path / "predictions.jsonl", [{**raw_record(), "answer_value": "mutated"}])

    with pytest.raises(ArtifactValidationError, match="predictions.jsonl must be identical"):
        validate_step15_artifacts(tmp_path)


def test_validate_artifacts_allows_documented_mutation(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path)
    write_jsonl(tmp_path / "predictions.jsonl", [{**raw_record(), "answer_value": "mutated"}])

    result = validate_step15_artifacts(tmp_path, allow_mutated_predictions=True)

    assert result["valid"] is True


def test_validate_artifacts_requires_row_alignment(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path)
    write_jsonl(tmp_path / "agent_overlays.jsonl", [])

    with pytest.raises(ArtifactValidationError, match="raw/overlay row count mismatch"):
        validate_step15_artifacts(tmp_path)


def test_validate_artifacts_requires_writeback_outputs_when_enabled(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path, writeback_enabled=True)

    with pytest.raises(ArtifactValidationError, match="writeback artifact missing"):
        validate_step15_artifacts(tmp_path)


def test_validate_manifest_11_success(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path, writeback_enabled=True, manifest_11=True)
    write_writeback_artifacts(tmp_path)

    result = validate_step15_artifacts(tmp_path)

    assert result["valid"] is True
    assert result["writeback_enabled"] is True


def test_validate_manifest_11_rejects_uncertain_without_evidence(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path, writeback_enabled=True, manifest_11=True, evidence_refs=[])
    write_writeback_artifacts(tmp_path)

    with pytest.raises(ArtifactValidationError, match="WB_MISSING_EVIDENCE"):
        validate_step15_artifacts(tmp_path)


def test_validate_manifest_11_rejects_summary_mismatch(tmp_path: Path) -> None:
    write_valid_artifacts(tmp_path, writeback_enabled=True, manifest_11=True, summary_override={"uncertain": 2})
    write_writeback_artifacts(tmp_path)

    with pytest.raises(ArtifactValidationError, match="writeback.summary.uncertain"):
        validate_step15_artifacts(tmp_path)


def test_validate_artifacts_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_valid_artifacts(tmp_path)

    main(["validate-artifacts", "--run-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert '"valid": true' in captured.out


def test_validate_artifacts_cli_args(tmp_path: Path) -> None:
    parser = build_parser()

    args = parser.parse_args(["validate-artifacts", "--run-dir", str(tmp_path), "--allow-mutated-predictions"])

    assert args.command == "validate-artifacts"
    assert args.run_dir == tmp_path
    assert args.allow_mutated_predictions is True


def write_valid_artifacts(
    run_dir: Path,
    *,
    writeback_enabled: bool = False,
    manifest_11: bool = False,
    evidence_refs: list[dict] | None = None,
    summary_override: dict | None = None,
) -> None:
    write_jsonl(run_dir / "predictions_raw.jsonl", [raw_record()])
    write_jsonl(run_dir / "predictions.jsonl", [raw_record()])
    write_jsonl(run_dir / "agent_overlays.jsonl", [overlay_record()])
    write_jsonl(run_dir / "predictions_agent_view.jsonl", [{**raw_record(), "agent_overlay": overlay_record()}])
    write_jsonl(run_dir / "review_items.jsonl", [])
    write_jsonl(run_dir / "trace.jsonl", [])
    write_json(run_dir / "trace_summary.json", {"total_fields": 1})
    write_json(run_dir / "summary.json", {"fields_total": 1})
    (run_dir / "run_summary.md").write_text("# Summary\n", encoding="utf-8")
    manifest = {
            "run_id": "run_1",
            "status": "completed",
            "engine": "step15_agent_overlay",
            "writeback_enabled": writeback_enabled,
            "artifacts": {
                "predictions_raw": "predictions_raw.jsonl",
                "predictions": "predictions.jsonl",
                "agent_overlays": "agent_overlays.jsonl",
                "predictions_agent_view": "predictions_agent_view.jsonl",
                "review_items": "review_items.jsonl",
                "trace": "trace.jsonl",
                "trace_summary": "trace_summary.json",
                "run_summary": "run_summary.md",
                "summary": "summary.json",
                "filled_form": "filled_form.xlsx" if writeback_enabled else None,
            },
            "counts": {"total_fields": 1},
        }
    if manifest_11:
        refs = [
            {
                "chunk_id": "chunk_main",
                "document_id": "doc_1",
                "object_key": "kb/xixian/doc.xlsx",
                "qdrant_point_id": "point_1",
                "source_type": "main_excel_capability",
                "source_anchor": "能力清单!H42",
                "sheet_name": "能力清单",
                "cell": "H42",
            }
        ] if evidence_refs is None else evidence_refs
        writeback_summary = {"confirmed": 0, "uncertain": 1, "flagged": 0, "written": 1, "review": 1}
        writeback_summary.update(summary_override or {})
        manifest.update(
            {
                "schema_version": "1.1",
                "writeback": {
                    "summary": writeback_summary,
                    "fields": [
                        {
                            "field_key": "item_4",
                            "field_id": "item_4",
                            "row_index": 4,
                            "target_cell": "D4",
                            "sheet_name": "Sheet1",
                            "cell": "D4",
                            "status": "uncertain",
                            "answer_status": "partial_clue",
                            "answer_value": "2路市电",
                            "writeback_action": "written_red_comment",
                            "evidence_refs": refs,
                        }
                    ],
                },
            }
        )
    write_json(run_dir / "run_manifest.json", manifest)


def write_writeback_artifacts(run_dir: Path) -> None:
    (run_dir / "filled_form.xlsx").write_bytes(b"xlsx")
    write_jsonl(run_dir / "writeback_audit.jsonl", [])
    write_json(run_dir / "evidence_map.json", {"fields": {}})


def raw_record() -> dict:
    return {
        "field_id": "item_4",
        "row_index": 4,
        "target_cell": "D4",
        "answer_value": "2路市电",
        "answer_status": "answered",
        "confidence": 0.9,
        "source_chunk_ids": ["chunk_main"],
        "evidence_attachment_ids": [],
        "reference_chunk_ids": [],
        "reference_source_documents": [],
        "reference_snippets": [],
        "validation": {},
        "method_name": "step15_agent",
    }


def overlay_record() -> dict:
    return {
        "field_id": "item_4",
        "row_index": 4,
        "target_cell": "D4",
        "critic_flags": [],
        "review_required": False,
        "writeback_allowed": True,
        "suggested_status": None,
        "suggested_answer_value": None,
        "suggested_reference_source_documents": [],
        "suggested_reference_chunk_ids": [],
        "suggested_reference_snippets": [],
        "risk_level": "low",
        "reasons": [],
    }
