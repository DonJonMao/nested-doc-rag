from __future__ import annotations

from copy import deepcopy

import pytest
from openpyxl import Workbook

from nested_doc_rag.artifacts import validate_manifest_13
from nested_doc_rag.config import app_config_from_dict
from nested_doc_rag.io import write_jsonl


def validate_cell_action(tmp_path, *, old=None, new="500kVA", actual="500kVA", policy="preserve", written=True, status="confirmed", cli=False, modify=None):
    workbook = Workbook()
    workbook.active.title = "调研"
    workbook.active["D2"] = actual
    workbook.save(tmp_path / "filled_form.xlsx")
    workbook.close()
    audit = {"field_id": "capacity", "sheet_name": "调研", "cell": "D2", "old_value": old,
             "new_value": new, "policy": policy, "status": status,
             "writeback_action": "written" if written else "skipped_non_empty_cell"}
    manifest = {"writeback_enabled": True, "writeback": {"config": {"existing_value_policy": policy}, "overwrite_all_cli": cli},
                "form_input": {"acquisition_contract": {"writeback_policy": {
                    "version": "writeback-policy-v1", "existing_value_policy": policy, "overwrite_all_cli": cli}}}}
    if modify:
        modify(audit, manifest)
    write_jsonl(tmp_path / "writeback_audit.jsonl", [audit])
    errors = []
    validate_manifest_13(tmp_path, manifest, errors)
    return errors


@pytest.mark.parametrize("old,new,policy,written", [(None, "500kVA", "preserve", True),
                                                   ("人工值", "人工值", "preserve", False),
                                                   ("人工值", "500kVA", "overwrite_confirmed", True),
                                                   ("=1+2", "=1+2", "overwrite_all", False)])
def test_valid_cell_actions_match_policy_and_actual_download(tmp_path, old, new, policy, written):
    assert validate_cell_action(tmp_path, old=old, new=new, actual=new, policy=policy, written=written, cli=policy == "overwrite_all") == []


@pytest.mark.parametrize("kwargs,diagnostic", [
    ({"old": "人工值"}, "cannot overwrite"),
    ({"old": "=1+2", "policy": "overwrite_all", "cli": True}, "formula changed"),
    ({"old": "人工值", "policy": "overwrite_confirmed", "status": "uncertain"}, "cannot overwrite"),
    ({"policy": "overwrite_all"}, "explicit frozen CLI"),
    ({"old": "人工值", "written": False}, "skipped target changed"),
    ({"actual": "替换下载文件中的值"}, "workbook value differs"),
    ({"old": False, "new": False, "actual": 0, "written": False}, "workbook value differs"),
])
def test_forged_actions_and_changed_workbook_are_rejected(tmp_path, kwargs, diagnostic):
    assert any(diagnostic in error for error in validate_cell_action(tmp_path, **kwargs))


def test_missing_audit_values_and_changed_frozen_policy_are_rejected(tmp_path):
    errors = validate_cell_action(tmp_path, modify=lambda audit, _: audit.pop("old_value"))
    assert any("incomplete cell-action audit" in error for error in errors)

    def change_contract(_, manifest):
        manifest["form_input"] = deepcopy(manifest["form_input"])
        manifest["form_input"]["acquisition_contract"]["writeback_policy"]["existing_value_policy"] = "overwrite_confirmed"

    errors = validate_cell_action(tmp_path, modify=change_contract)
    assert any("differs from the frozen" in error for error in errors)


@pytest.mark.parametrize("value", ["overwrite_all", "arbitrary", None, False])
def test_dangerous_or_unknown_policy_cannot_enter_from_configuration(tmp_path, value):
    with pytest.raises(ValueError, match="requires an explicit CLI"):
        app_config_from_dict({"writeback": {"existing_value_policy": value}}, project_root_base=tmp_path)
