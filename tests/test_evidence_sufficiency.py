from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest

from nested_doc_rag.agent.slotting import SlotDecomposition, SlotSpec
from nested_doc_rag.grounding.sufficiency import (
    SUFFICIENCY_PROMPT_VERSION,
    EvidenceSufficiency,
    build_sufficiency_messages,
    build_targeted_query,
    normalize_sufficiency,
)


def hit(evidence_id: str = "source-1") -> dict[str, Any]:
    source = "😀市电两路\r\n\tUPS额定容量 / 500 kVA  "
    return {
        "chunk_id": evidence_id,
        "namespace": "room301",
        "knowledge_base_id": "kb1",
        "file_name": "能力.xlsx",
        "sheet_name": "动力",
        "row_index": 4,
        "cell_range": "B4:C4",
        "evidence_kind": "structured_field",
        "raw_source_text": source,
        "raw_text": "合成摘要：UPS容量500kVA",
        "source_text_hash": "sha256:" + hashlib.sha256(source.encode()).hexdigest(),
        "source_text_hash_space": "raw_source_text",
        "field_name": "UPS容量",
        "field_value": "500 kVA",
    }


def response(**overrides: Any) -> dict[str, Any]:
    return {
        "sufficient": True,
        "missing_facts": [],
        "supporting_evidence_ids": ["source-1"],
        "reason": "当前目标机房原文包含UPS额定容量",
        **overrides,
    }


def prompt_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    return json.loads(messages[1]["content"].split("请检查当前证据包：\n", 1)[1].split("\n只输出以下四个字段：", 1)[0])


def codes(result: EvidenceSufficiency) -> set[str]:
    return {entry["code"] for entry in result.diagnostics}


def test_prompt_whitelists_form_and_slot_inputs_without_heldout_values() -> None:
    secret = "UNIQUE_HELDOUT_SECRET_739"
    item = {
        "form_item_id": "field-1",
        "question_text": "UPS配置情况",
        "instruction_text": "请填品牌、容量和冗余模式",
        "answer_example": "品牌；容量；模式",
        "category_path": ["动力", {"gold": secret}],
        "needs_evidence": True,
        "row_index": 4,
        "existing_value": secret,
        "heldout_value": secret,
        "gold": {"value": secret},
        "current_info": secret,
        "metadata": {"answer": secret},
        "file_name": {"heldout": secret},
    }
    schema = {
        "is_composite": True,
        "slots": [{
            "name": "capacity", "label": "UPS额定容量", "required": True,
            "value_type": "short_text", "evidence_required": True,
            "canonical_hints": [secret], "allowed_values": [secret], "gold": secret,
            "actual_value": secret, "confidence": 0.987, "metadata": {"answer": secret},
        }],
        "compose_rule": secret,
        "reasons": [secret],
        "confidence": 0.976,
    }

    messages = build_sufficiency_messages(item, [hit()], target_namespace="room301", slot_schema=schema)
    payload = prompt_payload(messages)

    assert SUFFICIENCY_PROMPT_VERSION == "evidence-sufficiency-v1"
    assert messages[0]["content"].startswith("Evidence Sufficiency Check")
    assert secret not in json.dumps(messages)
    assert payload["form_item"]["file_name"] == ""
    assert payload["form_item"]["category_path"] == ["动力"]
    assert payload["form_item"]["answer_example_format_only"] == "品牌；容量；模式"
    assert "answer_example" not in payload["form_item"]
    assert payload["slot_schema"]["slots"] == [{
        "name": "capacity", "label": "UPS额定容量", "required": True,
        "value_type": "short_text", "evidence_required": True,
    }]


def test_prompt_preserves_exact_native_text_and_address_without_score_signal() -> None:
    source = {
        **hit(), "evidence_id": "canonical-1", "vector_rank": 2, "vector_score": 0.876,
        "rerank_rank": 1, "rerank_score": 0.987, "layer_priority": 5,
        "retrieval_layer": "target_structured_fact", "retrieval_round": 0,
        "parent_payload": {"table_title": "UPS设备参数", "confidence": 0.999, "unit": "kVA"},
        "text_for_embedding": "EMBEDDING_SYNTHETIC_FACT_731",
    }
    original = deepcopy(source)

    payload = prompt_payload(build_sufficiency_messages({"question_text": "UPS容量"}, iter([source])))
    evidence = payload["retrieved_evidence"][0]

    assert evidence["evidence_id"] == "canonical-1"
    assert evidence["text"] == evidence["raw_source_text"] == source["raw_source_text"]
    assert evidence["address"]["file_name"] == "能力.xlsx"
    assert evidence["address"]["sheet_name"] == "动力"
    assert evidence["address"]["cell_range"] == "B4:C4"
    assert evidence["parent_payload"]["unit"] == "kVA"
    assert evidence["retrieval_round"] == 0
    assert evidence["source_text_authority"] == {"text_space": "raw_source_text", "policy": "native"}
    assert evidence["raw_text_policy"] == "auxiliary_non_authoritative"
    assert "text_for_embedding" not in evidence
    assert "EMBEDDING_SYNTHETIC_FACT_731" not in json.dumps(payload)
    for key in ("rank", "score", "layer_priority", "confidence"):
        assert key not in evidence
    assert "confidence" not in evidence["parent_payload"]
    assert source == original


def test_prompt_requires_metadata_and_synthetic_context_to_agree_with_native_source() -> None:
    source = {**hit(), "field_value": "3000 kVA", "raw_text": "UPS容量3000kVA", "parent_payload": {"parent_text": "UPS容量3000kVA"}}
    messages = build_sufficiency_messages({"question_text": "UPS容量"}, [source])
    evidence = prompt_payload(messages)["retrieved_evidence"][0]

    assert evidence["raw_source_text"] == source["raw_source_text"]
    assert evidence["raw_text_policy"] == "auxiliary_non_authoritative"
    assert "事实权威仅限raw_source_text原文" in messages[0]["content"]
    assert "其中事实必须逐项由权威原文核对" in messages[0]["content"]
    assert "不能依靠元数据、父级摘要或合成内容补齐缺失事实" in messages[0]["content"]


def test_prompt_marks_legacy_raw_text_authoritative_only_after_resolver_verification() -> None:
    source = hit()
    for key in ("raw_source_text", "source_text_hash", "source_text_hash_space"):
        source.pop(key)
    unverified = prompt_payload(build_sufficiency_messages({"question_text": "容量"}, [source]))["retrieved_evidence"][0]
    source["source_text_hash"] = "sha256:" + hashlib.sha256(source["raw_text"].encode()).hexdigest()
    source["source_text_hash_space"] = "raw_text"
    verified = prompt_payload(build_sufficiency_messages({"question_text": "容量"}, [source]))["retrieved_evidence"][0]
    source["source_text_hash"] = "sha256:bad"
    bad_hash = prompt_payload(build_sufficiency_messages({"question_text": "容量"}, [source]))["retrieved_evidence"][0]

    assert unverified["source_text_authority"] == bad_hash["source_text_authority"] == {"text_space": None, "policy": "unavailable"}
    assert unverified["raw_text_policy"] == "auxiliary_non_authoritative"
    assert verified["source_text_authority"] == {"text_space": "raw_text", "policy": "legacy_hash_verified"}
    assert verified["raw_text_policy"] == "legacy_hash_verified"
    assert verified["raw_text"] == source["raw_text"]


def test_prompt_preserves_nested_native_original_text_resolved_from_source() -> None:
    source = {
        "chunk_id": "paragraph-1", "file_name": "现场.docx", "evidence_kind": "paragraph",
        "raw_text": "合成摘要", "source": {"paragraph_index": 3, "raw_source_text": "😀原文\r\nUPS容量500kVA"},
    }
    evidence = prompt_payload(build_sufficiency_messages({"question_text": "容量"}, [source]))["retrieved_evidence"][0]

    assert evidence["source_text_authority"] == {"text_space": "raw_source_text", "policy": "native"}
    assert evidence["text"] == evidence["raw_source_text"] == source["source"]["raw_source_text"]
    assert evidence["raw_text_policy"] == "auxiliary_non_authoritative"


def test_prompt_accepts_slot_decomposition_object_and_ignores_answer_hints() -> None:
    schema = SlotDecomposition(True, [SlotSpec("capacity", "UPS额定容量", canonical_hints=["SECRET_HINT"])], confidence=0.98)
    payload = prompt_payload(build_sufficiency_messages({"question_text": "UPS配置"}, [], slot_schema=schema))

    assert payload["slot_schema"]["is_composite"] is True
    assert payload["slot_schema"]["slots"][0]["label"] == "UPS额定容量"
    assert "SECRET_HINT" not in json.dumps(payload)
    assert "confidence" not in payload["slot_schema"]


@pytest.mark.parametrize("bad_value", [{"gold": "SECRET"}, ["SECRET"], 3, True, None])
def test_prompt_does_not_stringify_nested_answer_examples(bad_value: Any) -> None:
    payload = prompt_payload(build_sufficiency_messages({"question_text": "容量", "answer_example": bad_value}, []))

    assert payload["form_item"]["answer_example_format_only"] is None
    assert "SECRET" not in json.dumps(payload)


def test_valid_sufficiency_resolves_support_and_deduplicates_exact_ids() -> None:
    result = normalize_sufficiency(response(supporting_evidence_ids=["source-1", "source-1"]), iter([hit()]))

    assert result.sufficient is True
    assert result.missing_facts == []
    assert result.supporting_evidence_ids == ["source-1"]
    assert result.diagnostics == []
    assert json.loads(json.dumps(result.to_dict(), ensure_ascii=False)) == result.to_dict()


def test_insufficient_result_keeps_valid_support_and_normalizes_missing_facts() -> None:
    result = normalize_sufficiency(response(sufficient=False, missing_facts=[" UPS冗余模式 ", "UPS冗余模式"]), [hit()])

    assert result.sufficient is False
    assert result.missing_facts == ["UPS冗余模式"]
    assert result.supporting_evidence_ids == ["source-1"]
    assert result.diagnostics == []


@pytest.mark.parametrize("value", [None, [], "{}", True, 1])
def test_nonobject_response_fails_closed_with_question_fallback(value: Any) -> None:
    result = normalize_sufficiency(value, [hit()], item={"question_text": "UPS配置", "gold": "SECRET"})

    assert result.sufficient is False
    assert result.supporting_evidence_ids == []
    assert result.missing_facts == ["UPS配置"]
    assert codes(result) == {"SUFFICIENCY_SCHEMA_INVALID"}
    assert "SECRET" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("overrides", [
    {"sufficient": "true"}, {"sufficient": 1}, {"sufficient": None},
    {"missing_facts": "capacity"}, {"missing_facts": [123]}, {"missing_facts": [""]},
    {"supporting_evidence_ids": ("source-1",)}, {"supporting_evidence_ids": [True]},
    {"supporting_evidence_ids": [" "]}, {"reason": " "}, {"reason": {"gold": "SECRET"}},
    {"unexpected": "value"},
])
def test_malformed_fields_fail_closed(overrides: dict[str, Any]) -> None:
    result = normalize_sufficiency(response(**overrides), [hit()], item={"question_text": "UPS容量"})

    assert result.sufficient is False
    assert "SUFFICIENCY_SCHEMA_INVALID" in codes(result)
    assert result.missing_facts == ["UPS容量"]


@pytest.mark.parametrize("field", ["sufficient", "missing_facts", "supporting_evidence_ids", "reason"])
def test_missing_output_fields_fail_closed(field: str) -> None:
    value = response()
    del value[field]
    result = normalize_sufficiency(value, [hit()])

    assert result.sufficient is False
    schema_error = next(entry for entry in result.diagnostics if entry["reason"] == "unexpected_or_missing_fields")
    assert schema_error["missing_fields"] == [field]


def test_true_with_missing_facts_is_contradictory() -> None:
    result = normalize_sufficiency(response(missing_facts=["UPS冗余模式"]), [hit()])

    assert result.sufficient is False
    assert result.missing_facts == ["UPS冗余模式"]
    assert result.supporting_evidence_ids == ["source-1"]
    assert codes(result) == {"SUFFICIENCY_CONTRADICTORY"}


def test_true_without_support_is_rejected_and_uses_only_required_slot_labels() -> None:
    schema = SlotDecomposition(True, [
        SlotSpec("brand", "UPS品牌", required=False, canonical_hints=["SECRET"]),
        SlotSpec("capacity", "UPS容量"), SlotSpec("redundancy", "UPS冗余模式"),
    ], reasons=["SECRET"])
    result = normalize_sufficiency(response(supporting_evidence_ids=[]), [hit()], item={"question_text": "UPS配置", "gold": "SECRET"}, slot_schema=schema)

    assert result.sufficient is False
    assert result.missing_facts == ["UPS容量", "UPS冗余模式"]
    assert codes(result) == {"SUFFICIENCY_SUPPORT_MISSING"}
    assert "SECRET" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("support_id", ["other-pack", " source-1 ", "source-1 "])
def test_support_must_be_exactly_in_this_pack(support_id: str) -> None:
    result = normalize_sufficiency(response(supporting_evidence_ids=[support_id]), [hit()])

    assert result.sufficient is False
    assert result.supporting_evidence_ids == []
    assert codes(result) == {"SUFFICIENCY_SUPPORT_NOT_IN_PACK", "SUFFICIENCY_SUPPORT_MISSING"}


def test_only_canonical_prompt_evidence_id_is_allowed_when_chunk_alias_differs() -> None:
    source = {**hit(), "evidence_id": "canonical-1"}
    by_canonical = normalize_sufficiency(response(supporting_evidence_ids=["canonical-1"]), [source])
    by_alias = normalize_sufficiency(response(), [source])

    assert by_canonical.sufficient is True
    assert by_canonical.supporting_evidence_ids == ["canonical-1"]
    assert by_alias.sufficient is False
    assert "SUFFICIENCY_SUPPORT_NOT_IN_PACK" in codes(by_alias)


@pytest.mark.parametrize("overrides", [
    {"sheet_name": None, "cell_range": None, "row_index": None},
    {"file_name": ""}, {"raw_source_text": " \r\n "},
    {"source_text_hash": "sha256:invalid"}, {"cell_range": "A0"},
    {"source": {"source_text_hash": "sha256:bad-nested"}},
])
def test_unaddressable_or_unverified_source_cannot_support_true(overrides: dict[str, Any]) -> None:
    result = normalize_sufficiency(response(), [{**hit(), **overrides}])

    assert result.sufficient is False
    assert result.supporting_evidence_ids == []
    assert codes(result) == {"SUFFICIENCY_SUPPORT_UNRESOLVABLE", "SUFFICIENCY_SUPPORT_MISSING"}
    assert next(entry for entry in result.diagnostics if entry["code"] == "SUFFICIENCY_SUPPORT_UNRESOLVABLE")["errors"]


def test_legacy_compressed_raw_text_is_rejected_without_its_declared_hash() -> None:
    source = hit()
    for key in ("raw_source_text", "source_text_hash", "source_text_hash_space"):
        source.pop(key)
    unverified = normalize_sufficiency(response(), [source])
    source["source_text_hash"] = "sha256:" + hashlib.sha256(source["raw_text"].encode()).hexdigest()
    source["source_text_hash_space"] = "raw_text"
    verified = normalize_sufficiency(response(), [source])

    assert unverified.sufficient is False
    assert "SUFFICIENCY_SUPPORT_UNRESOLVABLE" in codes(unverified)
    assert verified.sufficient is True


def test_conflicting_duplicate_origin_cannot_hide_behind_valid_hit() -> None:
    source = hit()
    conflict = {**source, "row_index": 5, "cell_range": "B5:C5"}
    result = normalize_sufficiency(response(), [source, conflict])

    assert result.sufficient is False
    problem = next(entry for entry in result.diagnostics if entry["code"] == "SUFFICIENCY_SUPPORT_UNRESOLVABLE")
    assert problem["errors"][0]["reason"] == "conflicting_retrieval_hits"


def test_one_invalid_support_fails_entire_check_even_with_other_valid_support() -> None:
    result = normalize_sufficiency(response(supporting_evidence_ids=["source-1", "invented"]), [hit()])

    assert result.sufficient is False
    assert result.supporting_evidence_ids == ["source-1"]
    assert codes(result) == {"SUFFICIENCY_SUPPORT_NOT_IN_PACK"}


def test_insufficient_empty_missing_list_uses_sanitized_mapping_slot_fallback() -> None:
    schema = {"slots": [
        {"label": "容量", "required": True, "gold": "SECRET"},
        {"label": "容量", "required": True}, {"label": "品牌", "required": False},
        {"label": {"gold": "SECRET"}, "required": True},
    ]}
    result = normalize_sufficiency(response(sufficient=False, supporting_evidence_ids=[]), [], slot_schema=schema)

    assert result.sufficient is False
    assert result.missing_facts == ["容量"]
    assert result.diagnostics == []
    assert "SECRET" not in json.dumps(result.to_dict())


def test_targeted_query_uses_missing_facts_and_room_scope_without_answer_values() -> None:
    item = {"question_text": "UPS配置情况", "answer_example": "SECRET", "existing_value": "SECRET", "gold": {"value": "SECRET"}}
    query = build_targeted_query(item, [" UPS容量 ", "UPS冗余模式", "UPS容量"], target_namespace="room301", room_context="三楼机房")

    assert query == "目标机房=room301。目标机房上下文=三楼机房。字段=UPS配置情况。缺失事实=UPS容量；UPS冗余模式"
    assert "SECRET" not in query
    assert build_targeted_query(item, [], target_namespace="room301") == "目标机房=room301。字段=UPS配置情况。缺失事实=UPS配置情况"


@pytest.mark.parametrize("facts", [None, "容量", ("容量",), [1], [""], [" "], [{"gold": "SECRET"}]])
def test_targeted_query_rejects_malformed_missing_facts(facts: Any) -> None:
    with pytest.raises(ValueError, match="nonempty missing facts"):
        build_targeted_query({"question_text": "UPS配置"}, facts, target_namespace="room301")


def test_targeted_query_requires_a_question_or_missing_fact() -> None:
    with pytest.raises(ValueError, match="field or missing fact"):
        build_targeted_query({"gold": "SECRET"}, [], target_namespace="room301")


def test_sufficiency_serialization_is_detached_from_mutable_nested_diagnostics() -> None:
    result = EvidenceSufficiency(False, ["UPS容量"], [], "来源不可解析", [{"code": "BAD", "errors": [{"reason": "original"}]}])
    dumped = result.to_dict()
    dumped["missing_facts"].append("forged")
    dumped["diagnostics"][0]["errors"][0]["reason"] = "mutated"

    assert result.missing_facts == ["UPS容量"]
    assert result.diagnostics[0]["errors"][0]["reason"] == "original"
