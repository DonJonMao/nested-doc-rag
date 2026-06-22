from __future__ import annotations

from pathlib import Path
from typing import Any

from nested_doc_rag.io import read_json, read_jsonl


class ArtifactValidationError(RuntimeError):
    """Raised when a Step15AgentRunner output directory breaks the frozen contract."""


REQUIRED_STEP15_ARTIFACTS = [
    "predictions_raw.jsonl",
    "predictions.jsonl",
    "agent_overlays.jsonl",
    "predictions_agent_view.jsonl",
    "review_items.jsonl",
    "trace.jsonl",
    "trace_summary.json",
    "run_summary.md",
    "summary.json",
    "run_manifest.json",
]

WRITEBACK_ARTIFACTS = ["filled_form.xlsx", "writeback_audit.jsonl", "evidence_map.json"]
WRITEBACK_STATUSES = {"confirmed", "uncertain", "flagged"}
WRITEBACK_ACTIONS = {
    "written",
    "written_red_comment",
    "review_only",
    "skipped_uncertain_policy",
    "skipped_non_empty_cell",
    "skipped_formula",
    "invalid_cell",
    "duplicate_target_cell",
}
WRITEBACK_ERROR_CODES = {
    "WB_INVALID_CELL",
    "WB_MISSING_EVIDENCE",
    "WB_OBJECT_NOT_FOUND",
    "WB_IMAGE_UPLOAD_FAILED",
    "WB_EMBED_IMAGE_FAILED",
    "WB_COMMENT_TOO_LONG",
    "WB_POLICY_REJECTED",
    "WB_MANIFEST_SCHEMA_INVALID",
}


def validate_step15_artifacts(run_dir: Path, *, allow_mutated_predictions: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    for name in REQUIRED_STEP15_ARTIFACTS:
        if not (run_dir / name).exists():
            errors.append(f"missing required artifact: {name}")

    raw_rows: list[dict[str, Any]] = []
    overlay_rows: list[dict[str, Any]] = []
    if (run_dir / "predictions_raw.jsonl").exists():
        raw_rows = read_jsonl(run_dir / "predictions_raw.jsonl")
    if (run_dir / "agent_overlays.jsonl").exists():
        overlay_rows = read_jsonl(run_dir / "agent_overlays.jsonl")

    if (run_dir / "predictions.jsonl").exists() and (run_dir / "predictions_raw.jsonl").exists() and not allow_mutated_predictions:
        if (run_dir / "predictions.jsonl").read_bytes() != (run_dir / "predictions_raw.jsonl").read_bytes():
            errors.append("predictions.jsonl must be identical to predictions_raw.jsonl")

    if (raw_rows or overlay_rows) and len(raw_rows) != len(overlay_rows):
        errors.append(f"raw/overlay row count mismatch: raw={len(raw_rows)} overlays={len(overlay_rows)}")

    manifest: dict[str, Any] = {}
    if (run_dir / "run_manifest.json").exists():
        manifest = read_json(run_dir / "run_manifest.json")
        artifacts = manifest.get("artifacts") or {}
        for key, value in artifacts.items():
            if value is None:
                continue
            path = run_dir / str(value)
            if not path.exists():
                errors.append(f"manifest artifact path missing: {key}={value}")
        if manifest.get("writeback_enabled"):
            for name in WRITEBACK_ARTIFACTS:
                if not (run_dir / name).exists():
                    errors.append(f"writeback artifact missing: {name}")
        if str(manifest.get("schema_version") or "1.0") == "1.1":
            validate_manifest_11(run_dir, manifest, errors)

    if errors:
        raise ArtifactValidationError("; ".join(errors))

    return {
        "run_dir": str(run_dir),
        "valid": True,
        "raw_rows": len(raw_rows),
        "overlay_rows": len(overlay_rows),
        "allow_mutated_predictions": allow_mutated_predictions,
        "manifest_status": manifest.get("status"),
        "writeback_enabled": bool(manifest.get("writeback_enabled")),
    }


def validate_manifest_11(run_dir: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    writeback = manifest.get("writeback")
    if not isinstance(writeback, dict):
        errors.append("WB_MANIFEST_SCHEMA_INVALID: missing writeback block")
        return
    fields = writeback.get("fields")
    if not isinstance(fields, list):
        errors.append("WB_MANIFEST_SCHEMA_INVALID: writeback.fields must be a list")
        return

    image_keys = image_evidence_keys(run_dir)
    status_counts = {"confirmed": 0, "uncertain": 0, "flagged": 0}
    written = 0
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: writeback.fields[{index}] must be an object")
            continue
        field_key = str(field.get("field_key") or field.get("field_id") or f"index_{index}")
        status = str(field.get("status") or "")
        action = str(field.get("writeback_action") or "")
        if status not in WRITEBACK_STATUSES:
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: invalid status for {field_key}: {status}")
        else:
            status_counts[status] += 1
        if action not in WRITEBACK_ACTIONS:
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: invalid writeback_action for {field_key}: {action}")
        if action in {"written", "written_red_comment"}:
            written += 1
        if field.get("cell") and not is_cell_ref(str(field.get("cell"))):
            errors.append(f"WB_INVALID_CELL: invalid cell for {field_key}: {field.get('cell')}")
        evidence_refs = field.get("evidence_refs") or []
        if not isinstance(evidence_refs, list):
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: evidence_refs for {field_key} must be a list")
            continue
        if status == "uncertain" and not evidence_refs:
            errors.append(f"WB_MISSING_EVIDENCE: uncertain field has no evidence_refs: {field_key}")
        for ref_index, ref in enumerate(evidence_refs):
            if not isinstance(ref, dict):
                errors.append(f"WB_MANIFEST_SCHEMA_INVALID: evidence_refs[{ref_index}] for {field_key} must be an object")
                continue
            for key_name in ("object_key", "image_object_key"):
                value = str(ref.get(key_name) or "")
                if value and unsafe_object_key(value):
                    errors.append(f"WB_OBJECT_NOT_FOUND: unsafe {key_name} for {field_key}: {value}")
            image_key = str(ref.get("image_object_key") or "")
            if image_key and image_keys and image_key not in image_keys:
                errors.append(f"WB_OBJECT_NOT_FOUND: image_object_key missing from image_evidence artifact for {field_key}: {image_key}")
        error_code = field.get("error_code")
        if error_code and str(error_code) not in WRITEBACK_ERROR_CODES:
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: invalid error_code for {field_key}: {error_code}")

    summary = writeback.get("summary") or {}
    expected = {
        "confirmed": status_counts["confirmed"],
        "uncertain": status_counts["uncertain"],
        "flagged": status_counts["flagged"],
        "written": written,
    }
    for key, value in expected.items():
        if summary.get(key) is not None and int(summary.get(key) or 0) != value:
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: writeback.summary.{key} does not match fields")


def image_evidence_keys(run_dir: Path) -> set[str]:
    path = run_dir / "image_evidence.jsonl"
    if not path.exists():
        return set()
    return {str(row.get("image_object_key")) for row in read_jsonl(path) if row.get("image_object_key")}


def is_cell_ref(value: str) -> bool:
    import re

    return bool(re.match(r"^[A-Z]{1,3}[1-9][0-9]*$", value))


def unsafe_object_key(value: str) -> bool:
    return value.startswith("/") or ".." in value.split("/")
