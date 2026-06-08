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
