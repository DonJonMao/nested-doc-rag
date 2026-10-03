from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from nested_doc_rag.evidence_resolver import is_typed_evidence_reference, resolve_evidence_refs, validate_evidence_ref
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
    "WB_TARGET_NON_EMPTY",
    "WB_OVERWRITE_POLICY",
    "WB_MANIFEST_SCHEMA_INVALID",
    "EV_REF_NOT_IN_RETRIEVAL",
    "EV_REF_MISSING_ADDRESS",
    "EV_CELL_RANGE_INVALID",
    "EV_FILE_NOT_IN_KB",
    "EV_ATTACHMENT_NOT_FOUND",
}
DEFAULT_MAX_COMMENT_CHARS = 2000


def validate_step15_artifacts(run_dir: Path, *, allow_mutated_predictions: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    review_evidence_diagnostics: list[str] = []
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
        version = str(manifest.get("schema_version") or "1.0")
        if version in {"1.1", "1.2", "1.3"}:
            validate_manifest_11(run_dir, manifest, errors)
        if version in {"1.2", "1.3"}:
            validate_manifest_12(run_dir, manifest, raw_rows, errors, review_evidence_diagnostics)
            if version == "1.3":
                validate_manifest_13(run_dir, manifest, errors)
        elif version not in {"1.0", "1.1"}:
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: unsupported manifest schema_version: {version}")

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
        "evidence_validation": "strict" if str(manifest.get("schema_version")) in {"1.2", "1.3"} else "legacy_read",
        "review_evidence_diagnostics": review_evidence_diagnostics,
    }


def validate_pinned_authority(
    run_dir: Path, manifest: dict[str, Any], authority: dict[str, list[dict[str, Any]]], errors: list[str],
) -> None:
    from nested_doc_rag.form.input_snapshot import SNAPSHOT_FILE, content_hash
    from nested_doc_rag.retrieval.version_scope import normalize_index_scopes, validate_hits_in_index_scopes

    snapshot = manifest.get("form_input") or {}
    acquisition = snapshot.get("acquisition_contract") or {}
    contract = acquisition.get("index_scope_contract")
    if contract is None and manifest.get("index_scopes") is None:
        return  # Historical addressed artifacts have no immutable-scope claim.
    try:
        if not isinstance(contract, dict) or contract.get("version") != "pinned-index-scopes-v1":
            raise ValueError("missing or unsupported index scope contract")
        collection = acquisition.get("collection_name")
        namespaces = [snapshot.get("target_namespace"), snapshot.get("global_namespace")]
        scopes = normalize_index_scopes(contract.get("scopes"), collection_name=collection, namespaces=namespaces)
        if scopes is not None and {scope["namespace"] for scope in scopes} != set(namespaces):
            raise ValueError("fill index scopes must contain exactly the target and global namespaces")
        if scopes != manifest.get("index_scopes"):
            raise ValueError("manifest scopes differ from the frozen form input")
        if scopes is not None and ((manifest.get("artifacts") or {}).get("index_scopes") != "index_scopes.json" or
                                   read_json(run_dir / "index_scopes.json") != scopes):
            raise ValueError("archived index scopes differ from the frozen form input")
        if read_json(run_dir / SNAPSHOT_FILE) != snapshot or content_hash(
            {key: value for key, value in snapshot.items() if key != "input_fingerprint"}
        ) != snapshot.get("input_fingerprint"):
            raise ValueError("frozen form input integrity mismatch")
        for hits in authority.values():
            validate_hits_in_index_scopes(hits, scopes, collection_name=collection,
                                         namespaces=namespaces if scopes is not None else None)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        errors.append(f"EV_INDEX_SCOPE_MISMATCH: {exc}")


def validate_manifest_12(
    run_dir: Path, manifest: dict[str, Any], raw_rows: list[dict[str, Any]], errors: list[str], diagnostics: list[str],
) -> None:
    """A new artifact must prove every reference against this run's hit pack."""
    artifact_name = (manifest.get("artifacts") or {}).get("retrieval_evidence")
    if not artifact_name or unsafe_object_key(str(artifact_name)):
        errors.append("EV_REF_NOT_IN_RETRIEVAL: manifest requires an in-run retrieval_evidence artifact")
        return
    authority_path = run_dir / str(artifact_name)
    if not authority_path.is_file():
        errors.append("EV_REF_NOT_IN_RETRIEVAL: retrieval_evidence artifact is missing")
        return
    try:
        authority_rows = read_jsonl(authority_path)
    except (ValueError, OSError):
        errors.append("WB_MANIFEST_SCHEMA_INVALID: unreadable retrieval_evidence artifact")
        return
    authority: dict[str, list[dict[str, Any]]] = {}
    for row in authority_rows:
        if not isinstance(row, dict) or not row.get("field_id") or not isinstance(row.get("top_hits"), list):
            errors.append("WB_MANIFEST_SCHEMA_INVALID: retrieval_evidence row requires field_id and top_hits")
            continue
        field_id = str(row["field_id"])
        if field_id in authority or not all(isinstance(hit, dict) for hit in row["top_hits"]):
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: duplicate or malformed retrieval evidence for {field_id}")
            continue
        authority[field_id] = row["top_hits"]

    validate_pinned_authority(run_dir, manifest, authority, errors)

    predictions: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        if not isinstance(row, dict) or not row.get("field_id"):
            errors.append("WB_MANIFEST_SCHEMA_INVALID: raw prediction requires a field_id")
            continue
        field_id = str(row["field_id"])
        if field_id in predictions:
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: duplicate raw field identity: {field_id}")
        predictions[field_id] = row
        if field_id not in authority:
            errors.append(f"EV_REF_NOT_IN_RETRIEVAL: missing retrieval evidence for field {field_id}")
        validate_ref_collection(row.get("evidence_refs"), authority.get(field_id, []), field_id, errors)
    if set(authority) - set(predictions):
        errors.append("WB_MANIFEST_SCHEMA_INVALID: retrieval evidence contains unknown field identities")

    writeback = manifest.get("writeback")
    fields = writeback.get("fields") or [] if isinstance(writeback, dict) else []
    audit_path = run_dir / "writeback_audit.jsonl"
    audit = read_jsonl(audit_path) if audit_path.is_file() else []
    for label, records in (("manifest", fields), ("audit", audit)):
        if not isinstance(records, list):
            continue
        seen: set[str] = set()
        for row in records:
            if not isinstance(row, dict):
                continue
            field_id = str(row.get("field_id") or row.get("field_key") or "")
            if not field_id or field_id not in predictions or field_id in seen:
                errors.append(f"WB_MANIFEST_SCHEMA_INVALID: unknown or duplicate {label} writeback field: {field_id}")
                continue
            seen.add(field_id)
            refs = row.get("evidence_refs")
            confirmed = row.get("status") == "confirmed"
            if confirmed and not refs:
                errors.append(f"WB_MISSING_EVIDENCE: confirmed {label} field has no evidence_refs: {field_id}")
            if confirmed:
                validate_ref_collection(refs, authority.get(field_id, []), field_id, errors, display_refs=True)
            elif isinstance(refs, list):
                typed = [ref for ref in refs if isinstance(ref, dict) and is_typed_evidence_reference(ref)]
                clues = [ref for ref in refs if ref not in typed]
                validate_ref_collection(typed, authority.get(field_id, []), field_id, errors, display_refs=True)
                if clues:
                    clue_errors: list[str] = []
                    validate_ref_collection(clues, authority.get(field_id, []), field_id, clue_errors, display_refs=True)
                    diagnostics.extend(f"REVIEW_DISPLAY_CLUE: {error}" for error in clue_errors)
                    if not clue_errors:
                        diagnostics.append(f"REVIEW_DISPLAY_CLUE: {field_id} contains legacy display references; no confirmed authorization")
            else:
                errors.append(f"WB_MANIFEST_SCHEMA_INVALID: evidence_refs must be an explicit list for {field_id}")
            if confirmed:
                raw_refs = predictions[field_id].get("evidence_refs") or []
                if not raw_refs:
                    errors.append(f"WB_MISSING_EVIDENCE: confirmed field has no raw typed reference: {field_id}")
                raw_ids = {str(ref.get("chunk_id") or "") for ref in raw_refs if isinstance(ref, dict)}
                selected_ids = {str(value) for value in predictions[field_id].get("source_chunk_ids") or []}
                if raw_ids != selected_ids:
                    errors.append(f"EV_REF_NOT_IN_RETRIEVAL: confirmed source IDs do not match raw typed references: {field_id}")
                attachment_ids = {
                    str(value) for ref in raw_refs if isinstance(ref, dict) for value in ref.get("attachment_ids") or []
                }
                if {str(value) for value in predictions[field_id].get("evidence_attachment_ids") or []} - attachment_ids:
                    errors.append(f"EV_ATTACHMENT_NOT_FOUND: selected attachment is not in raw typed references: {field_id}")
                if any(isinstance(ref, dict) and str(ref.get("chunk_id") or "") not in raw_ids for ref in refs or []):
                    errors.append(f"EV_REF_NOT_IN_RETRIEVAL: confirmed {label} evidence was not selected by raw prediction: {field_id}")
    if manifest.get("writeback_enabled") and isinstance(fields, list):
        manifest_index = {str(row.get("field_id") or row.get("field_key") or ""): row for row in fields if isinstance(row, dict)}
        audit_index = {str(row.get("field_id") or row.get("field_key") or ""): row for row in audit if isinstance(row, dict)}
        if manifest_index != audit_index:
            # Audits have additional operational fields; compare the contract
            # shared with the manifest rather than their full serialized rows.
            keys = ("status", "writeback_action", "evidence_refs", "target_cell", "answer_value", "error_code")
            if manifest.get("schema_version") == "1.3":
                keys += ("old_value", "new_value", "policy")
            if set(manifest_index) != set(audit_index) or any(
                any(manifest_index[field_id].get(key) != audit_index[field_id].get(key) for key in keys)
                for field_id in set(manifest_index) & set(audit_index)
            ):
                errors.append("WB_MANIFEST_SCHEMA_INVALID: manifest/audit writeback evidence or action mismatch")


def audit_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value) if isinstance(value, timedelta) else value


def same_audit_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def validate_manifest_13(run_dir: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    """Verify the explicit policy and its actual workbook actions."""
    if not manifest.get("writeback_enabled"):
        return
    writeback = manifest.get("writeback")
    if not isinstance(writeback, dict):
        errors.append("WB_OVERWRITE_POLICY: writeback policy block must be an object")
        return
    config = writeback.get("config")
    config = config if isinstance(config, dict) else {}
    policy = config.get("existing_value_policy")
    if policy not in {"preserve", "overwrite_confirmed", "overwrite_all"}:
        errors.append("WB_OVERWRITE_POLICY: manifest has no valid existing-cell policy")
    form_input = manifest.get("form_input")
    acquisition = form_input.get("acquisition_contract") if isinstance(form_input, dict) else None
    contract = acquisition.get("writeback_policy") if isinstance(acquisition, dict) else None
    contract = contract if isinstance(contract, dict) else {}
    if contract.get("version") != "writeback-policy-v1" or contract.get("existing_value_policy") != policy:
        errors.append("WB_OVERWRITE_POLICY: writeback policy differs from the frozen input contract")
    all_cli = writeback.get("overwrite_all_cli") is True
    if contract.get("overwrite_all_cli") is not all_cli or (policy == "overwrite_all" and not all_cli):
        errors.append("WB_OVERWRITE_POLICY: overwrite_all lacks its explicit frozen CLI selection")
    audit_path = run_dir / "writeback_audit.jsonl"
    workbook_path = run_dir / "filled_form.xlsx"
    if not audit_path.is_file() or not workbook_path.is_file():
        return  # Missing mandatory artifacts are reported by the common check.
    try:
        workbook = load_workbook(workbook_path, data_only=False)
    except Exception as exc:  # noqa: BLE001 - artifact corruption is a validation failure
        errors.append(f"WB_MANIFEST_SCHEMA_INVALID: cannot read filled workbook: {exc}")
        return
    try:
        for row in read_jsonl(audit_path):
            if not isinstance(row, dict):
                continue
            field_id = row.get("field_id", "")
            if any(key not in row for key in ("old_value", "new_value", "policy")):
                errors.append(f"WB_OVERWRITE_POLICY: incomplete cell-action audit for {field_id}")
                continue
            old, new = row["old_value"], row["new_value"]
            if row["policy"] != policy:
                errors.append(f"WB_OVERWRITE_POLICY: audit policy differs from manifest for {field_id}")
            written = row.get("writeback_action") in {"written", "written_red_comment"}
            nonempty = old is not None and old != ""
            if isinstance(old, str) and old.startswith("=") and (written or not same_audit_value(old, new)):
                errors.append(f"WB_OVERWRITE_POLICY: formula changed for {field_id}")
            if written and nonempty and (policy == "preserve" or (policy == "overwrite_confirmed" and row.get("status") != "confirmed")):
                errors.append(f"WB_TARGET_NON_EMPTY: policy cannot overwrite this target for {field_id}")
            if not written and not same_audit_value(old, new):
                errors.append(f"WB_OVERWRITE_POLICY: skipped target changed for {field_id}")
            sheet, cell = row.get("sheet_name"), row.get("cell")
            if sheet and cell:
                try:
                    actual = audit_value(workbook[sheet][cell].value)
                except (KeyError, ValueError, TypeError):
                    errors.append(f"WB_MANIFEST_SCHEMA_INVALID: invalid audited target for {field_id}")
                    continue
                if not same_audit_value(actual, new):
                    errors.append(f"WB_OVERWRITE_POLICY: workbook value differs from audit for {field_id}")
    finally:
        workbook.close()


def validate_ref_collection(
    refs: Any, hits: list[dict[str, Any]], field_id: str, errors: list[str], *, display_refs: bool = False,
) -> None:
    if not isinstance(refs, list):
        errors.append(f"WB_MANIFEST_SCHEMA_INVALID: evidence_refs must be an explicit list for {field_id}")
        return
    for ref in refs:
        if not isinstance(ref, dict):
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: evidence reference must be an object for {field_id}")
            continue
        matching = [hit for hit in hits if str(hit.get("chunk_id") or "") == str(ref.get("chunk_id") or "")]
        if not matching:
            errors.append(f"EV_REF_NOT_IN_RETRIEVAL: {field_id} reference not in its retrieval pack: {ref.get('chunk_id')}")
            continue
        if not str(ref.get("source_text") or "").strip():
            errors.append(f"WB_MISSING_EVIDENCE: source_text is empty for {field_id}")
        presentation_keys = {"image_object_key", "image_path", "media_path", "media_content_type", "proof_attachment_id", "cell", "caption"}
        canonical = {key: value for key, value in ref.items() if key not in presentation_keys} if display_refs else ref
        for hit in matching:
            errors.extend(f"{error['code']}: {error['reason']} (field {field_id})" for error in validate_evidence_ref(canonical, hit))
        if display_refs and any(ref.get(key) for key in ("image_object_key", "image_path", "media_path", "proof_attachment_id")):
            validate_image_presentation(ref, matching, field_id, errors)


def validate_image_presentation(ref: dict[str, Any], hits: list[dict[str, Any]], field_id: str, errors: list[str]) -> None:
    resolved = resolve_evidence_refs([str(ref.get("chunk_id") or "")], hits)
    if not resolved.resolvable:
        errors.append(f"EV_ATTACHMENT_NOT_FOUND: image has no resolvable source for {field_id}")
        return
    expected = resolved.refs[0]
    attachment_id = str(ref.get("proof_attachment_id") or "")
    if not attachment_id or attachment_id not in expected.attachment_ids:
        errors.append(f"EV_ATTACHMENT_NOT_FOUND: image attachment is not in the source hit for {field_id}")
        return
    attachment = next((item for item in expected.proof_attachments if item.get("attachment_id") == attachment_id), {})
    aliases = {
        "image_path": ("image_path", "image_file_path", "local_image_path", "preview_image_path", "artifact_path", "embedded_payload_path"),
        "media_path": ("media_path",), "media_content_type": ("media_content_type",), "cell": ("source_cell",),
        "caption": ("caption", "evidence_role"),
    }
    for key, names in aliases.items():
        actual = ref.get(key)
        expected_value = next((attachment[name] for name in names if attachment.get(name)), None)
        if actual and actual != expected_value:
            # Captions such as "proof attachment" are generated labels, while
            # file paths and source coordinates must come from source metadata.
            if key == "caption" and not expected_value and actual == "proof attachment":
                continue
            errors.append(f"EV_ATTACHMENT_NOT_FOUND: image {key} does not match source metadata for {field_id}")


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

    validate_writeback_audit(run_dir, writeback, errors)


def validate_writeback_audit(run_dir: Path, writeback: dict[str, Any], errors: list[str]) -> None:
    audit_path = run_dir / "writeback_audit.jsonl"
    if not audit_path.exists():
        return
    max_comment_chars = max_comment_chars_from_manifest(writeback)
    for index, row in enumerate(read_jsonl(audit_path), start=1):
        if not isinstance(row, dict):
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: writeback_audit row {index} must be an object")
            continue
        field_key = str(row.get("field_id") or row.get("field_key") or f"row_{index}")
        comment_length = row.get("comment_length")
        if comment_length is None:
            continue
        try:
            length = int(comment_length)
        except (TypeError, ValueError):
            errors.append(f"WB_MANIFEST_SCHEMA_INVALID: invalid comment_length for {field_key}: {comment_length}")
            continue
        if length > max_comment_chars:
            errors.append(f"WB_COMMENT_TOO_LONG: comment_length exceeds max_comment_chars for {field_key}: {length}>{max_comment_chars}")


def max_comment_chars_from_manifest(writeback: dict[str, Any]) -> int:
    config = writeback.get("config")
    candidates = []
    if isinstance(config, dict):
        candidates.append(config.get("max_comment_chars"))
    candidates.append(writeback.get("max_comment_chars"))
    for value in candidates:
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return DEFAULT_MAX_COMMENT_CHARS


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
