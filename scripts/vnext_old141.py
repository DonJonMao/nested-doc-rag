#!/usr/bin/env python3
"""Prepare or explicitly execute two closed-book old141 runs on copied stores.

Heldout human values are only an unverified, post-run agreement comparator.
No original Qdrant client is opened and preparation never calls a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from qdrant_client import QdrantClient

from nested_doc_rag.artifacts import ArtifactValidationError, validate_step15_artifacts
from nested_doc_rag.config import env_to_overrides, load_app_config, load_yaml_file

ROOT = Path(__file__).resolve().parents[1]
OLD_FILE = "基地云机房信息调研表.xlsx"
COLLECTION = "datacenter_chunks_v1"
EXPECTED_POINTS = 9533
DIMENSIONS = 4096
ROOM_CONTEXT = "西咸4号楼 301机房"
TARGET_NAMESPACE = "xixian_4"
GLOBAL_NAMESPACE = "global"
ITEM_KEYS = {"form_item_id", "file_name", "sheet_name", "row_index", "target_cell", "category_path",
             "question_text", "instruction_text", "needs_evidence", "answer_example"}
BOOTSTRAP = '''import sys
from nested_doc_rag import cli
from nested_doc_rag.config import load_app_config
def frozen_config(path=None, **kwargs):
    kwargs["env"] = {}
    return load_app_config(path, **kwargs)
cli.load_app_config = frozen_config
cli.main(sys.argv[1:])
'''


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def inventory(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("index must be a real directory")
    records = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("index symlinks cannot be frozen safely")
        if path.is_file():
            records.append({"path": str(path.relative_to(directory)), "size": path.stat().st_size, "sha256": sha256(path)})
    if not records:
        raise ValueError("source index is empty")
    return records


def descriptor(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("original inputs must be ordinary files")
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256(path)}


def original_inventory(original_root: Path) -> dict[str, Any]:
    return {
        "template": descriptor(original_root / "data/工勘单" / OLD_FILE),
        "form_items": descriptor(original_root / "artifacts/12_gongkan_form_analysis/form_items.jsonl"),
        "index_files": inventory(original_root / "artifacts/15_vector_store/qdrant"),
    }


def sanitize_environment(env: Mapping[str, str], credential_env: str) -> dict[str, str]:
    """Keep process essentials and the configured credential, never overrides."""
    allowed = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT", "WINDIR", "HOME",
               "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"}
    clean = {key: value for key, value in env.items() if key in allowed
             and not key.startswith(("NDR_", "NESTED_DOC_RAG__")) and not env_to_overrides({key: value})}
    if credential_env.startswith(("NDR_", "NESTED_DOC_RAG__")) or env_to_overrides({credential_env: "credential"}):
        raise ValueError("credential variable must not also be a configuration override")
    if credential_env in env:
        clean[credential_env] = env[credential_env]
    clean["PYTHONPATH"] = str(ROOT / "src")
    clean["PYTHONNOUSERSITE"] = "1"
    return clean


def read_models_environment(path: Path | None, base_env: Mapping[str, str]) -> dict[str, str]:
    """Read a private dotenv into a local mapping, without changing os.environ."""
    env = dict(base_env)
    if path is None:
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError("models-env requires dotenv variable assignments")
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("models-env has an unterminated quoted value")
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        env[key] = value
    return env


def inspect_copied_store(path: Path) -> dict[str, Any]:
    """Callers provide a verified copy; the original store is never opened."""
    client = QdrantClient(path=str(path))
    try:
        collection = client.get_collection(COLLECTION)
        vectors = collection.config.params.vectors
        if isinstance(vectors, dict):
            raise ValueError("old141 requires its original unnamed dense vector")
        dimensions = int(vectors.size)
        points = int(client.count(COLLECTION, exact=True).count)
        if dimensions != DIMENSIONS or points != EXPECTED_POINTS:
            raise ValueError(f"copied collection must contain {EXPECTED_POINTS} points with {DIMENSIONS} dimensions")
        return {"collection_name": COLLECTION, "dimensions": dimensions, "points_count": points}
    finally:
        client.close()


def validate_closed_book(path: Path) -> list[dict[str, Any]]:
    items = read_rows(path)
    if len(items) != 141 or {int(item["row_index"]) for item in items} != set(range(4, 145)):
        raise ValueError("closed-book input must contain exactly 141 rows, 4..144")
    if len({item["form_item_id"] for item in items}) != 141:
        raise ValueError("closed-book field identities must be unique")
    for item in items:
        if set(item) - ITEM_KEYS or item.get("answer_example") != "" or item.get("file_name") != OLD_FILE:
            raise ValueError("closed-book input contains nonwhitelisted facts or nonempty examples")
    targets = [target(item) for item in items]
    if len(set(targets)) != 141:
        raise ValueError("closed-book target addresses must be unique")
    return items


def target(item: Mapping[str, Any]) -> tuple[str, str]:
    raw = str(item["target_cell"])
    if "!" in raw:
        sheet, cell = raw.rsplit("!", 1)
        sheet = sheet.strip("'").replace("''", "'")
    else:
        sheet, cell = str(item["sheet_name"]), raw
    return sheet, cell.replace("$", "").upper()


def target_values(path: Path, items: list[dict[str, Any]]) -> dict[tuple[str, str], Any]:
    workbook = load_workbook(path, data_only=False)
    try:
        return {target(item): workbook[target(item)[0]][target(item)[1]].value for item in items}
    finally:
        workbook.close()


def prepare(original_root: Path, prepared_dir: Path, models_config: Path, out_dir: Path, *,
            models_kind: str, embedding_dimension: int, env: Mapping[str, str] | None = None,
            models_note: str | None = None, python: str | None = None) -> dict[str, Any]:
    original_root, prepared_dir, models_config, out_dir = (path.resolve() for path in
                                                        (original_root, prepared_dir, models_config, out_dir))
    if models_kind not in {"real", "stub"} or embedding_dimension != DIMENSIONS:
        raise ValueError("explicit models-kind and embedding dimension 4096 are required; no model fallback")
    if models_kind == "stub" and not str(models_note or "").strip():
        raise ValueError("stub models-kind requires an explicit nonempty models-note")
    if out_dir.is_relative_to(original_root) or out_dir.is_relative_to(prepared_dir):
        raise ValueError("output must be outside original and prepared inputs")
    if out_dir.exists():
        raise ValueError("each experiment requires a new output directory; existing output cannot be reused")
    raw_config = load_yaml_file(models_config)
    for key in ("embedding_dimension", "embedding_dimensions", "dimensions"):
        declared = (raw_config.get("services") or {}).get(key)
        if declared is not None and int(declared) != DIMENSIONS:
            raise ValueError("configured embedding dimension differs from the original 4096-dimension index")
    config = load_app_config(models_config, env={}).to_dict()
    credential = str(config["services"]["chat_api_key_env"])
    clean_env = sanitize_environment(os.environ if env is None else env, credential)
    before = original_inventory(original_root)
    preparation = json.loads((prepared_dir / "preparation.json").read_text())
    if preparation.get("field_count") != 141 or preparation.get("original_input_unchanged") is not True:
        raise ValueError("old141 prepared input declaration is invalid")
    for name, expected in preparation["generated_hashes"].items():
        if Path(name).name != name or sha256(prepared_dir / name) != expected:
            raise ValueError("prepared input hash differs from its frozen declaration")
    for name in ("template", "form_items"):
        original = before[name]
        if preparation["source_hashes"].get(original["path"]) != original["sha256"]:
            raise ValueError("prepared inputs refer to different original bytes")
    items_path = prepared_dir / "form_items_closed_book.jsonl"
    items = validate_closed_book(items_path)
    original_template = Path(before["template"]["path"])
    original_values = target_values(original_template, items)
    if any(value in (None, "") for value in original_values.values()):
        raise ValueError("original preserve template must contain all 141 human target values")
    blank_values = target_values(prepared_dir / "blank_target_template.xlsx", items)
    if any(value not in (None, "") and not (isinstance(original_values[key], str)
                                           and original_values[key].startswith("=") and value == original_values[key])
           for key, value in blank_values.items()):
        raise ValueError("blank template contains uncleared ordinary targets")
    out_dir.mkdir(parents=True)
    ledger: dict[str, Any] = {
        "schema_version": "old141-live-ledger-v1", "status": "preparing", "out_dir": str(out_dir),
        "original_root": str(original_root), "prepared_dir": str(prepared_dir), "models_kind": models_kind,
        "models_note": models_note or "Real-model execution requested only with --execute; not yet measured.",
        "runtime_kind": "copied_legacy_disk_qdrant", "data_origin": "legacy_old141_closed_book",
        "namespaces": {"target": TARGET_NAMESPACE, "global": GLOBAL_NAMESPACE}, "room_context": ROOM_CONTEXT,
        "source_code_sha256": sha256(Path(__file__)), "bootstrap_sha256": hashlib.sha256(BOOTSTRAP.encode()).hexdigest(),
        "models_config_sha256": sha256(models_config), "embedding_contract": {
            "declared_dimension": embedding_dimension, "provider_dimension_verified": False,
            "note": "Collection dimension is checked on copies; provider compatibility is not preflighted or inferred."},
        "source_originals_before": before, "groups": [], "model_requests": 0,
        "usage": "unknown; no provider usage is inferred", "cost": "unknown",
        "prepared_source_items_sha256": sha256(items_path),
        "prepared_input_hashes": preparation["generated_hashes"],
        "preparation_sha256": sha256(prepared_dir / "preparation.json"),
        "item_transform": "Qualify each native target with its declared sheet; whitelist and empty examples remain unchanged.",
        "environment": {"configuration_overrides": "removed; config loaded with env={} including the CLI bootstrap",
                        "passed_names": sorted(clean_env), "credential_variable": credential, "credential_values_archived": False},
        "quality_metrics": None, "heldout_note": "Heldout is read only after execution, for unverified agreement; never accuracy or prompt input.",
    }
    ledger_path = out_dir / "ledger.json"
    save(ledger_path, ledger)
    try:
        for name, template in (("blank", prepared_dir / "blank_target_template.xlsx"), ("preserve", original_template)):
            directory = out_dir / name
            inputs = directory / "inputs"
            inputs.mkdir(parents=True)
            copied_template = inputs / OLD_FILE
            copied_items = inputs / "form_items_closed_book.jsonl"
            shutil.copyfile(template, copied_template)
            qualified_items = []
            for item in items:
                sheet, cell = target(item)
                qualified_items.append({**item, "target_cell": "'" + sheet.replace("'", "''") + "'!" + cell})
            copied_items.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in qualified_items), encoding="utf-8")
            copied_store = directory / "qdrant"
            shutil.copytree(original_root / "artifacts/15_vector_store/qdrant", copied_store)
            copied_inventory = inventory(copied_store)
            if copied_inventory != before["index_files"]:
                raise ValueError("copied index file/size/hash differs from the frozen original")
            index_info = inspect_copied_store(copied_store)
            effective = deepcopy(config)
            effective["paths"] = {"project_root": str(directory), "data_dir": str(inputs), "artifacts_dir": str(directory / "artifacts"),
                                  "qdrant_path": str(copied_store), "temp_dir": str(directory / "tmp")}
            effective["qdrant"].update(url="", collection_name=COLLECTION)
            effective["retrieval"].update(target_namespace=TARGET_NAMESPACE, global_namespace=GLOBAL_NAMESPACE)
            effective["writeback"]["existing_value_policy"] = "preserve"
            effective["excel"].update(template_path=str(copied_template), output_path=str(directory / "run/filled_form.xlsx"))
            effective["evaluation"]["metrics_output"] = str(directory / "artifacts/offline_metrics.json")
            config_path = directory / "effective_config.yaml"
            save(config_path, effective)
            run_dir = directory / "run"
            command = [python or sys.executable, "-c", BOOTSTRAP, "run-step15-agent", "--config", str(config_path),
                       "--form-items", str(copied_items), "--template", str(copied_template), "--rows", "all",
                       "--room-context", ROOM_CONTEXT, "--target-namespace", TARGET_NAMESPACE, "--global-namespace", GLOBAL_NAMESPACE,
                       "--qdrant-path", str(copied_store), "--qdrant-collection", COLLECTION,
                       "--writeback", "--existing-value-policy", "preserve", "--no-judge", "--no-judge-cache", "--out-dir", str(run_dir)]
            ledger["groups"].append({"name": name, "template": str(copied_template), "template_sha256": sha256(copied_template),
                                     "form_items": str(copied_items), "form_items_sha256": sha256(copied_items),
                                     "qdrant_path": str(copied_store), "index_copy_verified": True,
                                     "copy_inventory_before_inspection": copied_inventory, "index_info": index_info,
                                     "index_snapshot_after_inspection": inventory(copied_store),
                                     "effective_config": effective, "config_path": str(config_path), "config_sha256": sha256(config_path),
                                     "command": command, "command_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(),
                                     "run_dir": str(run_dir), "execution": None})
            save(ledger_path, ledger)
        ledger["status"] = "prepared"
        return ledger
    except Exception as exc:
        ledger.update(status="preparation_failed", failure=str(exc))
        raise
    finally:
        ledger["source_originals_after"] = original_inventory(original_root)
        ledger["original_input_unchanged"] = ledger["source_originals_after"] == before
        if not ledger["original_input_unchanged"]:
            ledger.update(status="preparation_failed", failure="original inputs changed during preparation")
        save(ledger_path, ledger)
        if not ledger["original_input_unchanged"]:
            raise RuntimeError("original inputs changed during preparation")


def run_command(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> dict[str, Any]:
    started = time.monotonic()
    with log.open("wb") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
    return {"command": command, "exit_code": result.returncode, "elapsed_seconds": round(time.monotonic() - started, 3),
            "log": str(log), "log_sha256": sha256(log)}


def compare_heldout(run_dir: Path, heldout: Path) -> dict[str, Any]:
    raw = {row["field_id"]: row for row in read_rows(run_dir / "predictions_raw.jsonl")}
    compared, equal = 0, 0
    for row in read_rows(heldout):
        if row.get("gold_verified") is not False or row.get("gold_origin") != "legacy_heldout_unverified":
            raise ValueError("heldout cannot be presented as independently verified gold")
        prediction = raw.get(row["field_id"])
        if prediction is not None:
            compared += 1
            equal += int(str(prediction.get("answer_value")) == str(row.get("expected_value")))
    return {"label": "unverified_heldout_agreement", "gold_verified": False, "quality_metrics": None,
            "compared_count": compared, "agreement_count": equal, "missing_count": 141 - compared}


def execute(ledger: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    if ledger["status"] != "prepared":
        raise ValueError("only a fresh prepared experiment can execute")
    out_dir, original = Path(ledger["out_dir"]), Path(ledger["original_root"])
    credential = ledger["environment"]["credential_variable"]
    clean_env = sanitize_environment(os.environ if env is None else env, credential)
    if sha256(Path(__file__)) != ledger["source_code_sha256"] or original_inventory(original) != ledger["source_originals_before"]:
        raise ValueError("frozen script or original input identity changed before execution")
    ledger.update(status="running", model_requests="not measured; execute may issue configured service requests")
    save(out_dir / "ledger.json", ledger)
    try:
        for group in ledger["groups"]:
            run_dir = Path(group["run_dir"])
            if run_dir.exists():
                raise ValueError("run output must be new; no resume or reuse")
            if (hashlib.sha256(json.dumps(group["command"]).encode()).hexdigest() != group["command_sha256"]
                    or inventory(Path(group["qdrant_path"])) != group["index_snapshot_after_inspection"]):
                raise ValueError("frozen command or copied index changed before execution")
            for path_key, hash_key in (("template", "template_sha256"), ("form_items", "form_items_sha256"), ("config_path", "config_sha256")):
                if sha256(Path(group[path_key])) != group[hash_key]:
                    raise ValueError("frozen run input changed before execution")
            result = run_command(group["command"], out_dir, clean_env, out_dir / (group["name"] + ".log"))
            group["execution"] = result
            if result["exit_code"] == 0:
                try:
                    group["artifact_validation"] = validate_step15_artifacts(run_dir)
                    manifest = json.loads((run_dir / "run_manifest.json").read_text())
                    group["runtime_failed_fields"] = (manifest.get("counts") or {}).get("failed")
                    if group["runtime_failed_fields"] != 0:
                        raise ValueError("runtime reported failed fields; no provider-compatible or successful-run claim")
                    items = validate_closed_book(Path(group["form_items"]))
                    predictions = read_rows(run_dir / "predictions_raw.jsonl")
                    if len(predictions) != 141 or {row["field_id"] for row in predictions} != {item["form_item_id"] for item in items}:
                        raise ValueError("runtime predictions do not cover exactly the 141 closed-book fields")
                    heldout = Path(ledger["prepared_dir"]) / "heldout_answers.jsonl"
                    if sha256(heldout) != ledger["prepared_input_hashes"][heldout.name]:
                        raise ValueError("unverified heldout comparator changed after preparation")
                    group["heldout_comparison"] = compare_heldout(run_dir, heldout)
                    if group["name"] == "preserve":
                        baseline = target_values(Path(group["template"]), items)
                        actual = target_values(run_dir / "filled_form.xlsx", items)
                        changed = sum(actual[key] != value for key, value in baseline.items())
                        group["preserve_141_targets"] = {"checked_count": len(baseline), "changed_count": changed, "valid": changed == 0}
                        if changed:
                            raise ValueError("preserve changed original human targets")
                except (ArtifactValidationError, ValueError, OSError) as exc:
                    group["validation_error"] = str(exc)
            save(out_dir / "ledger.json", ledger)
        ledger["status"] = "completed" if all(group["execution"]["exit_code"] == 0 and not group.get("validation_error")
                                               for group in ledger["groups"]) else "failed"
        return ledger
    except Exception as exc:
        ledger.update(status="failed", failure=str(exc))
        raise
    finally:
        ledger["source_originals_after"] = original_inventory(original)
        ledger["original_input_unchanged"] = ledger["source_originals_after"] == ledger["source_originals_before"]
        if not ledger["original_input_unchanged"]:
            ledger.update(status="failed", failure="original inputs changed during execution")
        save(out_dir / "ledger.json", ledger)
        if not ledger["original_input_unchanged"]:
            raise RuntimeError("original inputs changed during execution")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, default=Path("/Users/mao/projects/datacenter"))
    parser.add_argument("--prepared-dir", type=Path, default=ROOT / "artifacts/vnext/phase5/old141-prepared")
    parser.add_argument("--models-config", type=Path, required=True)
    parser.add_argument("--models-env", type=Path, help="Private dotenv read into a local credential mapping; overrides are discarded.")
    parser.add_argument("--models-kind", choices=["real", "stub"], required=True)
    parser.add_argument("--models-note")
    parser.add_argument("--embedding-dimension", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--execute", action="store_true", help="Explicitly issue model requests after preparing both isolated runs.")
    args = parser.parse_args()
    if args.models_kind == "stub" and not str(args.models_note or "").strip():
        parser.error("--models-kind stub requires an explicit nonempty --models-note")
    env = read_models_environment(args.models_env, os.environ)
    ledger = prepare(args.original_root, args.prepared_dir, args.models_config, args.out_dir,
                     models_kind=args.models_kind, embedding_dimension=args.embedding_dimension,
                     env=env, models_note=args.models_note, python=args.python)
    if args.models_env:
        ledger["models_env_source"] = {"path": str(args.models_env.resolve()), "sha256": sha256(args.models_env),
                                       "values_archived": False, "use": "credential mapping only; no configuration overrides"}
        save(Path(ledger["out_dir"]) / "ledger.json", ledger)
    if args.execute:
        ledger = execute(ledger, env=env)
    print(json.dumps({"ledger": str(args.out_dir.resolve() / "ledger.json"), "status": ledger["status"],
                      "models_kind": ledger["models_kind"], "model_requests": ledger["model_requests"],
                      "original_input_unchanged": ledger["original_input_unchanged"]}))
    return 0 if ledger["status"] in {"prepared", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
