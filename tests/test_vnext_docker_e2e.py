import hashlib
import json
import runpy
from pathlib import Path

import pytest
from openpyxl import Workbook

DRIVER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/vnext_docker_e2e.py"))


class ResponseLedger:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return self.response


@pytest.mark.parametrize("response", [
    {"status": "completed", "raw_status": "succeeded"},
    {"status": "succeeded"},
])
def test_wait_success_accepts_actual_domain_success(response):
    ledger = ResponseLedger(response)
    assert DRIVER["wait_success"](ledger, "/fill-runs/run", "fill", 0.001, 0) is response
    assert ledger.calls == 1


@pytest.mark.parametrize("raw_status", ["failed", "completed_with_failures", "canceled"])
def test_display_completion_cannot_hide_domain_failure(raw_status):
    ledger = ResponseLedger({"status": "completed", "raw_status": raw_status})
    with pytest.raises(DRIVER["AcceptanceError"], match=f"terminal status is {raw_status}"):
        DRIVER["wait_success"](ledger, "/fill-runs/run", "fill", 0.001, 0)
    assert ledger.calls == 1


def test_fill_inspection_uses_creation_pins_and_detail_progress(tmp_path):
    template = tmp_path / "uploaded.xlsx"
    workbook = Workbook()
    workbook.active.title = "调研"
    workbook.active["A1"] = "保留人工值"
    workbook.save(template)
    workbook.save(tmp_path / "filled_form.xlsx")
    workbook.close()
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    pair = {"id": "f1", "collection": "shared", "namespaces": {"target": "room", "global": "global"},
            "template": {"expected_fields": 1, "sha256": digest, "absolute_path": str(template)}}
    scopes = [{"knowledge_base_id": role, "index_version_id": role + "-v1", "namespace": namespace,
               "collection": "shared", "storage_contract": "versioned_v1"}
              for role, namespace in pair["namespaces"].items()]
    created = {"target_scope": scopes[0], "global_scope": scopes[1]}
    # GET FillRunDetail intentionally has neither out_dir nor frozen scopes.
    detail = {"status": "completed", "raw_status": "succeeded", "progress_total": 1, "progress_done": 1}
    snapshot = {"selected_field_count": 1, "template_sha256": digest, "input_mode": "template",
                "target_namespace": "room", "global_namespace": "global"}
    (tmp_path / "form_input_snapshot.json").write_text(json.dumps(snapshot))
    (tmp_path / "index_scopes.json").write_text(json.dumps(scopes))
    (tmp_path / "predictions_raw.jsonl").write_text('{"field_id":"current-field"}\n')
    checks = DRIVER["inspect_fill"](None, pair, created, detail, tmp_path)
    assert checks["selected_field_count"] == checks["prediction_count"] == 1
    assert checks["unsafe_overwrite_count"] == 0
    created["target_scope"] = {**scopes[0], "index_version_id": "other-v2"}
    with pytest.raises(DRIVER["AcceptanceError"], match="frozen index scopes differ"):
        DRIVER["inspect_fill"](None, pair, created, detail, tmp_path)
