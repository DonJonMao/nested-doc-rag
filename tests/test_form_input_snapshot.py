from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nested_doc_rag.form.input_snapshot import (
    FormInputMismatchError,
    build_form_input_snapshot,
    persist_form_input_snapshot,
    serialized_form_items,
    validate_form_input_snapshot,
)
from nested_doc_rag.gongkan_eval import build_masked_query, select_form_items
from nested_doc_rag.io import read_json, write_json


def fields() -> list[dict]:
    return [
        {"file_name": "new.xlsx", "sheet_name": sheet, "row_index": 2, "target_cell": f"'{sheet}'!D2", "question_text": "市电路数"}
        for sheet in ("301机房", "401机房")
    ]


def test_multiple_sheets_with_same_row_keep_distinct_selected_identity() -> None:
    selected = select_form_items(fields(), [2])
    assert len(selected) == 2
    assert len({item["form_item_id"] for item in selected}) == 2
    assert {item["target_cell"] for item in selected} == {"'301机房'!D2", "'401机房'!D2"}
    snapshot = build_form_input_snapshot(selected)
    assert snapshot["selected_field_count"] == 2
    assert snapshot["form_items_sha256"] == hashlib.sha256(serialized_form_items(selected).encode()).hexdigest()


def test_missing_and_tampered_snapshot_cannot_authorize_resume(tmp_path: Path) -> None:
    snapshot = build_form_input_snapshot(select_form_items(fields(), None))
    with pytest.raises(FormInputMismatchError, match="missing"):
        validate_form_input_snapshot(tmp_path, snapshot, resume=True)
    persist_form_input_snapshot(tmp_path, snapshot, resume=False)
    validate_form_input_snapshot(tmp_path, snapshot, resume=True)
    stored = read_json(tmp_path / "form_input_snapshot.json")
    stored["selected_field_count"] = 99
    write_json(tmp_path / "form_input_snapshot.json", stored)
    with pytest.raises(FormInputMismatchError, match="integrity"):
        validate_form_input_snapshot(tmp_path, snapshot, resume=True)


@pytest.mark.parametrize("change", ["template", "parser", "selection", "scope", "content"])
def test_each_material_input_change_rejects_resume(tmp_path: Path, change: str) -> None:
    template = tmp_path / "template.xlsx"
    template.write_bytes(b"template bytes")
    items = select_form_items(fields(), None)
    original = build_form_input_snapshot(items, template_path=template, target_namespace="target")
    persist_form_input_snapshot(tmp_path, original, resume=False)
    options = {"template_path": template, "target_namespace": "target"}
    if change == "template":
        template.write_bytes(b"changed bytes")
    elif change == "parser":
        options["parser_version"] = "2"
    elif change == "selection":
        options["selected_items"] = items[:1]
    elif change == "scope":
        options["target_namespace"] = "another"
    else:
        items[0]["instruction_text"] = "填写规划数据"
    changed = build_form_input_snapshot(items, **options)
    with pytest.raises(FormInputMismatchError, match="changed"):
        validate_form_input_snapshot(tmp_path, changed, resume=True)
    assert read_json(tmp_path / "form_input_snapshot.json") == original


def test_masked_query_uses_dynamic_form_and_excludes_actual_target_answers() -> None:
    item = dict(fields()[0], target_column_label="实际情况", heldout_answer="SECRET_GOLD", existing_value="SECRET_ACTUAL")
    query = build_masked_query(item, "room_301")
    assert "new.xlsx" in query and "实际情况" in query
    assert "SECRET" not in query
    assert "最后一列" not in query


def test_older_evidence_contract_cannot_resume_with_same_form_fields(tmp_path: Path) -> None:
    from nested_doc_rag.form.input_snapshot import content_hash

    current = build_form_input_snapshot(select_form_items(fields(), None))
    previous = {key: value for key, value in current.items() if key not in {"input_fingerprint", "evidence_contract_version"}}
    previous["input_fingerprint"] = content_hash(previous)
    write_json(tmp_path / "form_input_snapshot.json", previous)
    with pytest.raises(FormInputMismatchError, match="evidence contract.*changed"):
        validate_form_input_snapshot(tmp_path, current, resume=True)
