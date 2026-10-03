#!/usr/bin/env python3
"""Run actual historical A0–A3 commits with common, heldout-free field inputs.

This is a retrieval-method experiment, separate from fresh Docker acceptance.
The small adapter bypasses historical filename/row selection only; it invokes
the real stage ingestion, runner, retrieval, prompts, writer and validator.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from nested_doc_rag.config import env_to_overrides, load_app_config, load_dotenv_file, load_yaml_file
from nested_doc_rag.form.template_parser import parse_form_template

COMMITS = {"A0": "030300065e0d5041f581f573e772f7756e54abd6", "A1": "300832c", "A2": "e41ef99", "A3": "ac91742"}
ROOT = Path(__file__).resolve().parents[1]
# This explicit experimental adapter keeps the pinned A3 first acquisition,
# sufficiency check, hit annotations and answer gate; it only omits supplement.
# It is not a production configuration knob or an unmodified historical entry.
PRIMARY_COUNTERFACTUAL_ADAPTER = '''def install_primary_counterfactual(runner):
 if not runner.sufficiency_enabled or runner.mas_mode!="off":
  raise ValueError("A3 primary counterfactual requires sufficiency enabled and MAS off")
 contract_kind="A3-primary-only-no-supplement-v1"
 original_contract=runner.acquisition_contract
 def acquisition_contract():
  return {**original_contract(),"counterfactual":contract_kind}
 def collect_primary_only(item,query,decomposition):
  field_id=stage_module.field_id_for_item(item)
  configured_layers={str(spec["layer_name"]) for spec in runner.layered_plan}
  primary_layers=[name for name in ("target_structured_fact","target_table_detail") if name in configured_layers]
  started=stage_module.perf_counter_ms()
  primary=runner.retrieve(query,layer_names=primary_layers)
  retrieval_latency=stage_module.perf_counter_ms()-started
  hits=stage_module.tag_acquisition_hits(runner.attach_parent_payloads(primary.reranked_hits),0,[])
  vectors=stage_module.tag_acquisition_hits(runner.attach_parent_payloads(primary.vector_hits),0,[])
  rounds=[{"retrieval_round":0,"query":query,"hit_count":len(hits),**(primary.metadata or {})}]
  runner.trace.record(field_id,"primary_retrieval_completed",rounds[0])
  started=stage_module.perf_counter_ms()
  sufficient=runner.check_sufficiency(item,hits,decomposition,retrieval_round=0)
  sufficiency_latency=stage_module.perf_counter_ms()-started
  metadata={"strategy":"sufficiency_guided","counterfactual":contract_kind,"acquisition_rounds":1,"rounds":rounds,
   "qdrant_query_calls":rounds[0].get("qdrant_query_calls"),"retrieval_attempts":int(rounds[0].get("retrieval_attempts",1)),
   "retrieval_latency_ms":round(retrieval_latency,3),"sufficiency_latency_ms":round(sufficiency_latency,3),
   "final_sufficiency":sufficient.to_dict(),"conflicting_evidence":[]}
  return stage_module.Step15RetrievalResult(reranked_hits=hits,vector_hits=vectors,retrieval_mode=runner.retrieval_plan,
   metadata=metadata),sufficient
 runner.acquisition_contract=acquisition_contract
 runner.collect_sufficient_evidence=collect_primary_only
'''
ADAPTER = '''import json,sys
from pathlib import Path
from nested_doc_rag.config import load_app_config
from nested_doc_rag.agent.step15_runner import Step15AgentRunner
import nested_doc_rag.agent.step15_runner as stage_module
''' + PRIMARY_COUNTERFACTUAL_ADAPTER + '''
config_path,items_path,template_path,out_dir,target,global_ns,room = sys.argv[1:8]
if sys.argv[8:] not in ([],["--primary-only"]):
 raise ValueError("unsupported experiment adapter mode")
config=load_app_config(config_path,env={})
items=[json.loads(s) for s in Path(items_path).read_text().splitlines() if s.strip()]
captures={}
original_retrieval=stage_module.run_step15_retrieval
def observe(query_text,**kwargs):
 result=original_retrieval(query_text,**kwargs)
 matching=[item for item in items if item.get("question_text") and item["question_text"] in query_text]
 if matching:
  item=max(matching,key=lambda item:len(item["question_text"]))
  captures[item["form_item_id"]]={"field_id":item["form_item_id"],"target_cell":item["target_cell"],"top_hits":result.reranked_hits,"vector_hits":result.vector_hits}
 return result
stage_module.run_step15_retrieval=observe
runner=Step15AgentRunner(config=config,target_namespace=target,global_namespace=global_ns,
 out_dir=Path(out_dir),room_context=room,template_path=Path(template_path),writeback_enabled=True,
 judge_enabled=False,prompt_version="step15_compat",chat_max_retries=0,timeout_seconds=30)
if sys.argv[8:]==["--primary-only"]:
 install_primary_counterfactual(runner)
predictions=runner.run(items)
capture_path=Path(out_dir).with_name(Path(out_dir).name+"_evaluation_retrieval.jsonl")
capture_path.write_text("".join(json.dumps(row,ensure_ascii=False)+"\\n" for row in captures.values()))
print(json.dumps({"field_count":len(predictions)},ensure_ascii=False))
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def export_source(commit: str, directory: Path) -> str:
    resolved = subprocess.check_output(["git", "rev-parse", commit], cwd=ROOT, text=True).strip()
    if directory.exists():
        if not (directory / ".vnext-commit").is_file() or (directory / ".vnext-commit").read_text().strip() != resolved:
            raise ValueError("existing export has a different identity")
        (directory / "_vnext_method_adapter.py").write_text(ADAPTER)
        return resolved
    data = subprocess.check_output(["git", "archive", resolved, "src", "config", "pyproject.toml", "README.md"], cwd=ROOT)
    directory.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            target = (directory / member.name).resolve()
            if not target.is_relative_to(directory.resolve()) or member.issym() or member.islnk():
                raise ValueError("unexpected Git archive path")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                assert stream is not None
                target.write_bytes(stream.read())
    (directory / ".vnext-commit").write_text(resolved + "\n")
    (directory / "_vnext_method_adapter.py").write_text(ADAPTER)
    return resolved


def run_command(command: list[str], cwd: Path, env: dict[str, str], log: Path) -> dict[str, Any]:
    started = time.monotonic()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, check=False)
    return {"command": command, "exit_code": result.returncode, "elapsed_seconds": round(time.monotonic() - started, 3), "log": str(log), "log_sha256": sha(log)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--models-config", type=Path, default=ROOT / "config/docker.yaml")
    parser.add_argument("--models-env", type=Path)
    parser.add_argument("--models-kind", choices=["real", "stub"], required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys_executable())
    parser.add_argument("--methods", nargs="+", choices=list(COMMITS), default=list(COMMITS))
    parser.add_argument("--execute", action="store_true", help="Without this flag only freeze configs/data/commands.")
    parser.add_argument("--primary-counterfactual", action="store_true")
    args = parser.parse_args()
    if args.models_env:
        load_dotenv_file(args.models_env)
    model_config = load_app_config(args.models_config.resolve()).to_dict()
    manifest = json.loads(args.manifest.read_text())
    path_base = args.manifest.resolve().parent if manifest.get("path_base") == "manifest directory" else args.dataset_dir.resolve()
    output = args.out_dir.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("each experiment requires a new empty output directory; retain earlier evidence")
    ledger: dict[str, Any] = {
        "schema_version": "vnext-ablation-ledger-v1", "runtime_kind": "native_local_qdrant",
        "models_kind": args.models_kind, "runner_kind": "actual_stage_Step15AgentRunner",
        "data_origin": "independent_synthetic_challenge", "dataset_manifest_sha256": sha(args.manifest),
        "models_config_sha256": sha(args.models_config), "models": model_config["services"],
        "usage": "unknown; historical clients discard usage", "cost": "unknown",
        "adapter": "Common vNext deterministic parser supplies identical items directly to runner.run; no heldout values/examples. Historical filename/row selection is bypassed only for the method comparison.",
        "retrieval_observation": "Read-only wrapper captures original stage run_step15_retrieval return values outside the run; no parameter, call, filter, hit or answer modification. Production authority remains primary where present.",
        "adapter_sha256": hashlib.sha256(ADAPTER.encode()).hexdigest(),
        "writeback_policy": "Historical stage safe writer on generated fixture copies; all A0–A3 precede preserve policy. Final vNext safety is evaluated separately.",
        "primary_counterfactual_control": {
            "requested": args.primary_counterfactual,
            "mode": "A3-primary-only-no-supplement-v1",
            "implementation": "Explicit experimental adapter; historical src is unchanged. Same A3 config, first retrieval, sufficiency check, acquisition annotations, answer prompt and gate; only supplement is disabled.",
            "adapter_sha256": hashlib.sha256(PRIMARY_COUNTERFACTUAL_ADAPTER.encode()).hexdigest(),
        },
        "method_differences": {"A0": "Exact base source_type-only uploaded retrieval plan; historical masked-query template text.", "A1": "Unified evidence schema, uploaded parser and evidence-kind layered plan.", "A2": "Addressable refs and archive authority; includes Phase1B, whose field selection is held common by adapter.", "A3": "Sufficiency guided primary + at most one supplement; schema-first disabled."},
        "runs": [],
    }
    save(output / "ledger.json", ledger)
    for method in args.methods:
        stage = output / "sources" / method
        commit = export_source(COMMITS[method], stage)
        for pair in manifest["pairs"]:
            pair_id = pair.get("pair_id", pair.get("dataset_id", pair.get("id")))
            if not pair_id:
                raise ValueError("pair identity is required")
            run_root = output / method / str(pair_id)
            target, global_ns = f"vnext_{pair_id}_target", f"vnext_{pair_id}_global"
            config = load_yaml_file(stage / "config/docker.yaml")
            config["services"] = model_config["services"]
            config["paths"] = {"project_root": str(stage), "data_dir": str(run_root / "inputs"), "artifacts_dir": str(run_root / "artifacts"), "qdrant_path": str(run_root / "qdrant"), "temp_dir": str(run_root / "tmp")}
            config["qdrant"] = {**model_config["qdrant"], "url": "", "collection_name": "phase5_ablation"}
            config["agentscope"] = {"enabled": False, "mode": "off"}
            config["agentic_mas"] = {"enabled": False}
            config["retrieval"]["sufficiency_enabled"] = method == "A3"
            config["retrieval"]["schema_first_enabled"] = False
            config["services"]["timeout_seconds"] = 30
            config["grounding"] = model_config["grounding"]
            template_rel = pair.get("template_path", pair.get("template"))
            if isinstance(template_rel, dict):
                template_rel = template_rel["path"]
            template = (path_base / template_rel).resolve()
            template_declaration = pair.get("template")
            if isinstance(template_declaration, dict) and sha(template) != str(template_declaration.get("sha256", "")).removeprefix("sha256:"):
                raise ValueError("template differs from the fixed dataset hash")
            items = parse_form_template(template).items
            for item in items:
                item.pop("existing_value", None)
                item["answer_example"] = None
            run_root.mkdir(parents=True, exist_ok=True)
            save(run_root / "namespace_map.json", {str(pair_id): target, "global": global_ns})
            items_path = run_root / "common_form_items.jsonl"
            items_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items))
            config_path = run_root / "effective_config.yaml"
            save(config_path, config)
            # Ingest CLI also loads its config. Remove every recognized host
            # override so it cannot change the frozen method or local storage.
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("NDR_", "NESTED_DOC_RAG__")) and not env_to_overrides({key: value})}
            credential_env = str(model_config["services"]["chat_api_key_env"])
            if credential_env in os.environ:
                env[credential_env] = os.environ[credential_env]
            env["PYTHONPATH"] = str(stage / "src")
            entry = {"method": method, "dataset_id": pair_id, "commit": commit, "template_sha256": sha(template), "items_sha256": sha(items_path), "config_sha256": sha(config_path), "namespaces": {"target": target, "global": global_ns}, "namespace_map_path": str(run_root / "namespace_map.json"), "field_count": len(items), "steps": [], "status": "prepared"}
            commands = []
            entry["sources"] = []
            for role, namespace in (("target", target), ("global", global_ns)):
                documents = pair.get(f"{role}_documents", pair.get(f"{role}_sources", []))
                if not documents:
                    raise ValueError(f"missing {role} sources for {pair_id}")
                source_dir = run_root / "inputs" / role
                source_dir.mkdir(parents=True, exist_ok=True)
                for document in documents:
                    relative = document["path"] if isinstance(document, dict) else document
                    source = (path_base / relative).resolve()
                    if isinstance(document, dict) and sha(source) != str(document.get("sha256", "")).removeprefix("sha256:"):
                        raise ValueError("knowledge source differs from the fixed dataset hash")
                    destination = source_dir / source.name
                    if destination.exists():
                        raise ValueError("source basename collision")
                    shutil.copyfile(source, destination)
                    entry["sources"].append({"role": role, "file_name": source.name, "sha256": sha(destination), "size_bytes": destination.stat().st_size})
                kb = str(uuid5(NAMESPACE_URL, f"phase5/{pair_id}/{role}"))
                commands.append([args.python, "-m", "nested_doc_rag.cli", "ingest-knowledge", "--config", str(config_path), "--input-dir", str(source_dir), "--namespace", namespace, "--knowledge-base-id", kb, "--out-dir", str(run_root / f"ingest_{role}")])
            commands.append([args.python, "_vnext_method_adapter.py", str(config_path), str(items_path), str(template), str(run_root / "run"), target, global_ns, str(pair.get("room_context") or pair_id)])
            commands.append([args.python, "-m", "nested_doc_rag.cli", "validate-artifacts", "--run-dir", str(run_root / "run")])
            entry["commands"] = commands
            ledger["runs"].append(entry)
            save(output / "ledger.json", ledger)
            if args.execute:
                entry["status"] = "running"
                for index, command in enumerate(commands):
                    step = run_command(command, stage, env, run_root / f"step_{index}.log")
                    entry["steps"].append(step)
                    save(output / "ledger.json", ledger)
                    if step["exit_code"] != 0:
                        entry["status"] = "failed"
                        break
                else:
                    entry["status"] = "completed"
                if method == "A3" and entry["status"] == "completed" and args.primary_counterfactual:
                    primary = json.loads(json.dumps(config))
                    primary_path = run_root / "primary_config.yaml"
                    save(primary_path, primary)
                    command = [args.python, "_vnext_method_adapter.py", str(primary_path), str(items_path), str(template), str(run_root / "primary_run"), target, global_ns, str(pair.get("room_context") or pair_id), "--primary-only"]
                    entry["primary_counterfactual"] = {
                        **run_command(command, stage, env, run_root / "primary.log"),
                        "mode": "A3-primary-only-no-supplement-v1", "commit": commit,
                        "config_sha256": sha(primary_path),
                        "adapter_sha256": hashlib.sha256(PRIMARY_COUNTERFACTUAL_ADAPTER.encode()).hexdigest(),
                    }
                    if entry["primary_counterfactual"]["exit_code"] != 0:
                        entry["status"] = "failed"
                        entry["failure_stage"] = "primary_counterfactual"
                save(output / "ledger.json", ledger)
            print(json.dumps({"method": method, "dataset_id": pair_id, "status": entry["status"]}))
    return 1 if any(row["status"] == "failed" for row in ledger["runs"]) else 0


def sys_executable() -> str:
    import sys
    return sys.executable


if __name__ == "__main__":
    raise SystemExit(main())
