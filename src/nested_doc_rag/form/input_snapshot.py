"""Freeze form input identity before any checkpoint or output is reused."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from nested_doc_rag.io import read_json, write_json

SNAPSHOT_FILE = "form_input_snapshot.json"
EXPLICIT_ITEMS_VERSION = "form-items-v1"
EVIDENCE_CONTRACT_VERSION = "addressed-evidence-v1"
ACQUISITION_CONTRACT_VERSION = "sufficiency-guided-v1"


def build_acquisition_contract(config: Any, *, prompt_version: str = "step15_compat", collection_name: str | None = None, layered_plan: list[dict[str, Any]] | None = None, allowed_layers: list[str] | None = None, overwrite_all_cli: bool = False, index_scopes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from nested_doc_rag.grounding.sufficiency import SUFFICIENCY_PROMPT_VERSION
    from nested_doc_rag.retrieval.field_schema import FIELD_SCHEMA_CONTRACT_VERSION
    from nested_doc_rag.retrieval.version_scope import normalize_index_scopes

    enabled = bool(config.retrieval.sufficiency_enabled)
    mode = config.agentscope.mode if config.agentscope.enabled or config.agentscope.mode != "off" else "off"
    if enabled and mode != "off":
        raise ValueError("sufficiency-guided retrieval requires agentscope.mode=off; explicitly disable sufficiency for legacy MAS")
    return {
        "version": ACQUISITION_CONTRACT_VERSION if enabled else "legacy-layered-v1",
        "sufficiency_enabled": enabled, "mas_mode": mode,
        "schema_first_enabled": bool(config.retrieval.schema_first_enabled),
        "field_schema_contract_version": FIELD_SCHEMA_CONTRACT_VERSION if config.retrieval.schema_first_enabled else None,
        "writeback_policy": {"version": "writeback-policy-v1", "existing_value_policy": config.writeback.existing_value_policy,
                             "overwrite_all_cli": overwrite_all_cli},
        "answer_prompt_version": prompt_version,
        "agentic_prompt_version": config.agentic_mas.prompt_version if mode == "agentic_mas" else None,
        "sufficiency_prompt_version": SUFFICIENCY_PROMPT_VERSION if enabled else None,
        "layered_plan_sha256": content_hash(layered_plan if layered_plan is not None else config.retrieval.layered_plan),
        "allowed_layers": list(allowed_layers if allowed_layers is not None else config.retrieval.query_layers),
        "collection_name": collection_name or config.qdrant.collection_name,
        "index_scope_contract": {"version": "pinned-index-scopes-v1", "scopes": normalize_index_scopes(
            index_scopes, collection_name=collection_name or config.qdrant.collection_name)},
    }


class FormInputMismatchError(RuntimeError):
    pass


def content_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def serialized_form_items(items: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for item in items)


def template_hash(template_path: Path | None) -> str | None:
    if template_path is None:
        return None
    if not template_path.is_file():
        raise RuntimeError(f"template does not exist: {template_path}")
    with template_path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def build_form_input_snapshot(
    items: list[dict[str, Any]],
    *,
    selected_items: list[dict[str, Any]] | None = None,
    template_path: Path | None = None,
    input_mode: str = "programmatic",
    parser_name: str = "explicit-form-items",
    parser_version: str = EXPLICIT_ITEMS_VERSION,
    rows_spec: str = "all",
    target_namespace: str = "",
    global_namespace: str = "",
    room_context: str | None = None,
    acquisition_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = selected_items if selected_items is not None else items
    ids = [str(item.get("form_item_id") or f"row_{item.get('row_index')}") for item in selected]
    if len(ids) != len(set(ids)):
        raise RuntimeError("form input requires unique field identities")
    snapshot: dict[str, Any] = {
        "schema_version": "1.0", "input_mode": input_mode,
        "evidence_contract_version": EVIDENCE_CONTRACT_VERSION,
        "acquisition_contract": dict(acquisition_contract or {}),
        "template_sha256": template_hash(template_path),
        "parser_name": parser_name, "parser_version": parser_version,
        "form_items_sha256": hashlib.sha256(serialized_form_items(items).encode("utf-8")).hexdigest(),
        "selected_field_ids": ids, "selected_field_count": len(ids),
        "rows_spec": rows_spec.strip().lower() or "all",
        "target_namespace": target_namespace, "global_namespace": global_namespace,
        "room_context": (room_context or "").strip(),
    }
    snapshot["input_fingerprint"] = content_hash(snapshot)
    return snapshot


def validate_form_input_snapshot(out_dir: Path, snapshot: dict[str, Any], *, resume: bool) -> None:
    if not resume:
        return
    path = out_dir / SNAPSHOT_FILE
    if not path.is_file():
        # Even an empty historical checkpoint directory has no proved identity.
        raise FormInputMismatchError("cannot resume: form input snapshot is missing; start a new run directory")
    try:
        previous = read_json(path)
    except (ValueError, OSError) as exc:
        raise FormInputMismatchError("cannot resume: form input snapshot is unreadable") from exc
    if not isinstance(previous, dict) or previous.get("input_fingerprint") != snapshot["input_fingerprint"]:
        raise FormInputMismatchError("cannot resume: template, parser, form items, selected fields, evidence contract or retrieval scope changed")
    prior_content = {key: value for key, value in previous.items() if key != "input_fingerprint"}
    if content_hash(prior_content) != previous["input_fingerprint"]:
        raise FormInputMismatchError("cannot resume: form input snapshot integrity check failed")


def persist_form_input_snapshot(out_dir: Path, snapshot: dict[str, Any], *, resume: bool) -> None:
    validate_form_input_snapshot(out_dir, snapshot, resume=resume)
    write_json(out_dir / SNAPSHOT_FILE, snapshot)
