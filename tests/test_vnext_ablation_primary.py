from __future__ import annotations

import hashlib
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ABLATION = runpy.run_path(str(ROOT / "scripts/vnext_ablation.py"))

# Run in the exported revision's interpreter path so these checks exercise A3,
# rather than importing today's runner under historical configuration flags.
PROBE = '''import json,sys
from copy import deepcopy
from pathlib import Path
from nested_doc_rag.config import load_app_config,load_yaml_file
import nested_doc_rag.agent.step15_runner as stage_module

payload=json.loads(Path(sys.argv[1]).read_text())
config_data=load_yaml_file(Path("config/docker.yaml"))
config_data["agentscope"]={"enabled":False,"mode":"off"}
config_data["grounding"].update({"evidence_strength_enabled":False,"field_binding_enabled":False,
 "field_binding_agent_enabled":False,"slot_decomposition_enabled":False,"pre_writeback_consistency_enabled":False})
config_data["retrieval"]["sufficiency_enabled"]=True
if payload["mode"]=="legacy":
 config_data["retrieval"]["sufficiency_enabled"]=False
 config_data["retrieval"]["layered_plan"]=[layer for layer in config_data["retrieval"]["layered_plan"]
  if layer.get("layer_name")=="target_structured_fact"]
config_path=Path(payload["out_dir"])/"config.json"
config_path.parent.mkdir(parents=True,exist_ok=True)
config_path.write_text(json.dumps(config_data))
config=load_app_config(config_path,env={})
calls,checks,answers=[],[],[]
def hit(identifier,kind,layer,text,row):
 return {"chunk_id":identifier,"evidence_id":identifier,"namespace":"room","knowledge_base_id":"kb",
  "evidence_kind":kind,"source_type":"uploaded_excel_row","corpus_layer":"fact","file_name":"knowledge.xlsx",
  "sheet_name":"能力","row_index":row,"cell_range":f"A{row}:B{row}","raw_source_text":text,"raw_text":text,
  "retrieval_layer":layer,"layer_priority":row,"field_name":"UPS容量","field_value":"500kVA"}
def retrieve(query,**kwargs):
 call={k:deepcopy(v) for k,v in kwargs.items() if k not in {"retriever","reranker"}}
 call["query"]=query
 calls.append(call)
 layers={layer["layer_name"] for layer in kwargs["layered_plan"]}
 pack=[]
 if "target_structured_fact" in layers:
  pack.append(hit("fact","structured_field","target_structured_fact","UPS容量：500kVA",2))
 if "target_table_detail" in layers:
  pack.append(hit("table","table_row","target_table_detail","UPS容量 / 500kVA",3))
 return stage_module.Step15RetrievalResult(pack,deepcopy(pack),"layered",{"qdrant_query_calls":len(layers),"retrieval_attempts":1})
def assess(**kwargs):
 checks.append(deepcopy(kwargs))
 missing=payload["missing"] and kwargs["retrieval_round"]==0
 return {"sufficient":not missing,"missing_facts":["UPS容量"] if missing else [],
  "supporting_evidence_ids":[h["chunk_id"] for h in kwargs["hits"]],"reason":"当前原文缺少必要事实" if missing else "当前原文覆盖必需事实"}
def answer(**kwargs):
 answers.append(deepcopy(kwargs))
 return {"answer_status":"answered","answer_value":"500kVA","confidence":0.9,"source_chunk_ids":["table"]}
stage_module.run_step15_retrieval=retrieve
runner=stage_module.Step15AgentRunner(config=config,out_dir=Path(payload["out_dir"]),target_namespace="room",
 global_namespace="global",room_context="301机房",retriever=object(),reranker=object(),
 sufficiency_caller=assess,answer_caller=answer,parent_payload_enabled=False,chat_max_retries=0)
if payload["mode"]=="primary":
 namespace={"stage_module":stage_module}
 exec(payload["primary_adapter"],namespace)
 namespace["install_primary_counterfactual"](runner)
item={"form_item_id":"ups","file_name":"new.xlsx","sheet_name":"调研","row_index":2,"target_cell":"'调研'!D2",
 "question_text":"UPS容量","instruction_text":"填写UPS容量","category_path":["动力","UPS"],"answer_example":None,"needs_evidence":True}
result=runner.process_item(item)
print(json.dumps({"calls":calls,"checks":checks,"answers":answers,"top_hits":result.top_hits,
 "prediction":result.prediction.to_dict(),"overlay":result.overlay.to_dict(),"generated":result.generated,
 "acquisition_contract":runner.acquisition_contract(),"source_revision":Path(".vnext-commit").read_text().strip()},ensure_ascii=False))
'''


def source_hashes(stage: Path) -> dict[str, str]:
    return {str(path.relative_to(stage)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (stage / "src").rglob("*.py")}


def run_probe(stage: Path, out_dir: Path, *, mode: str, missing: bool, adapter: str = "") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = out_dir / "probe-input.json"
    payload.write_text(json.dumps({"out_dir": str(out_dir), "mode": mode, "missing": missing, "primary_adapter": adapter}))
    probe = out_dir / "probe.py"
    probe.write_text(PROBE)
    result = subprocess.run(
        [sys.executable, str(probe), str(payload)], cwd=stage,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(stage / "src")},
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def historical_a3(tmp_path_factory: pytest.TempPathFactory) -> Path:
    stage = tmp_path_factory.mktemp("a3-primary") / "source"
    ABLATION["export_source"](ABLATION["COMMITS"]["A3"], stage)
    return stage


def test_legacy_counterfactual_drops_table_candidates_and_sufficiency(historical_a3: Path, tmp_path: Path) -> None:
    baseline = run_probe(historical_a3, tmp_path / "baseline", mode="baseline", missing=False)
    legacy = run_probe(historical_a3, tmp_path / "legacy", mode="legacy", missing=False)
    assert [layer["layer_name"] for layer in baseline["calls"][0]["layered_plan"]] == [
        "target_structured_fact", "target_table_detail",
    ]
    assert [layer["layer_name"] for layer in legacy["calls"][0]["layered_plan"]] == ["target_structured_fact"]
    assert [hit["chunk_id"] for hit in baseline["top_hits"]] == ["fact", "table"]
    assert [hit["chunk_id"] for hit in legacy["top_hits"]] == ["fact"]
    assert baseline["checks"] and not legacy["checks"]
    assert baseline["answers"][0]["messages"] != legacy["answers"][0]["messages"]


def test_primary_sufficient_preserves_actual_a3_candidates_messages_and_gate(historical_a3: Path, tmp_path: Path) -> None:
    before = source_hashes(historical_a3)
    baseline = run_probe(historical_a3, tmp_path / "baseline", mode="baseline", missing=False)
    primary = run_probe(
        historical_a3, tmp_path / "primary", mode="primary", missing=False,
        adapter=ABLATION["PRIMARY_COUNTERFACTUAL_ADAPTER"],
    )
    assert primary["source_revision"] == baseline["source_revision"]
    assert primary["calls"] == baseline["calls"]
    assert primary["checks"] == baseline["checks"]
    assert primary["answers"] == baseline["answers"]
    assert primary["top_hits"] == baseline["top_hits"]
    assert primary["overlay"] == baseline["overlay"]
    assert all(hit["retrieval_round"] == 0 and hit["triggered_by"] == [] for hit in primary["top_hits"])
    assert primary["acquisition_contract"]["counterfactual"] == "A3-primary-only-no-supplement-v1"
    assert before == source_hashes(historical_a3)


def test_primary_gap_retains_first_check_and_abstention_without_supplement(historical_a3: Path, tmp_path: Path) -> None:
    baseline = run_probe(historical_a3, tmp_path / "baseline", mode="baseline", missing=True)
    primary = run_probe(
        historical_a3, tmp_path / "primary", mode="primary", missing=True,
        adapter=ABLATION["PRIMARY_COUNTERFACTUAL_ADAPTER"],
    )
    assert len(baseline["calls"]) == len(baseline["checks"]) == 2
    assert len(primary["calls"]) == len(primary["checks"]) == 1
    assert primary["calls"][0] == baseline["calls"][0]
    assert primary["checks"][0] == baseline["checks"][0]
    assert not primary["answers"]
    assert primary["generated"]["origin"] == "system_sufficiency_abstention"
    assert primary["prediction"]["answer_status"] == "partial_clue"
    assert not primary["overlay"]["writeback_allowed"]
    acquisition = primary["prediction"]["validation"]["acquisition"]
    assert acquisition["acquisition_rounds"] == 1
    assert not acquisition["final_sufficiency"]["sufficient"]
    assert acquisition["rounds"][0]["qdrant_query_calls"] is None


@pytest.mark.parametrize("primary_exit", [0, 7])
def test_main_records_same_config_and_propagates_primary_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primary_exit: int,
) -> None:
    original_manifest = ROOT / "docs/vnext/datasets/manifest.json"
    manifest = json.loads(original_manifest.read_text())
    manifest["pairs"] = manifest["pairs"][:1]
    pair = manifest["pairs"][0]
    for record in [pair["template"], *pair["target_sources"], *pair["global_sources"]]:
        record["path"] = str((original_manifest.parent / record["path"]).resolve())
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    output = tmp_path / "experiment"

    def run_command(command, cwd, env, log):
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("Explicitly injected subprocess outcome; no service or CLI runner executed.\n")
        return {
            "command": command, "exit_code": primary_exit if command[-1] == "--primary-only" else 0,
            "elapsed_seconds": 0, "log": str(log), "log_sha256": ABLATION["sha"](log),
        }

    monkeypatch.setitem(ABLATION["main"].__globals__, "run_command", run_command)
    monkeypatch.setattr(sys, "argv", [
        "vnext_ablation.py", "--dataset-dir", str(ROOT / "artifacts/vnext/phase5/datasets"),
        "--manifest", str(manifest_path), "--models-config", str(ROOT / "config/docker.yaml"),
        "--models-kind", "stub", "--out-dir", str(output), "--methods", "A3", "--primary-counterfactual", "--execute",
    ])
    assert ABLATION["main"]() == int(primary_exit != 0)
    ledger = json.loads((output / "ledger.json").read_text())
    entry = ledger["runs"][0]
    primary = entry["primary_counterfactual"]
    assert primary["command"][-1] == "--primary-only"
    assert primary["mode"] == "A3-primary-only-no-supplement-v1"
    assert primary["commit"] == entry["commit"]
    assert primary["config_sha256"] == entry["config_sha256"]
    assert (output / "A3/f1/primary_config.yaml").read_bytes() == (output / "A3/f1/effective_config.yaml").read_bytes()
    assert primary["exit_code"] == primary_exit
    assert entry["status"] == ("failed" if primary_exit else "completed")
    if primary_exit:
        assert entry["failure_stage"] == "primary_counterfactual"
