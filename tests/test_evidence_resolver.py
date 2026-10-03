from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace

import pytest

from nested_doc_rag.evidence_resolver import resolve_evidence_refs, validate_evidence_ref


def hit() -> dict:
    text = "😀市电：两路。\r\n\tUPS容量 / 500 kVA  "
    return {
        "chunk_id": "real-source", "evidence_id": "real-source", "namespace": "room301", "knowledge_base_id": "kb1",
        "source_type": "uploaded_excel_row", "evidence_kind": "structured_field", "corpus_layer": "fact",
        "file_name": "能力清单.xlsx", "relative_path": "知识库/能力清单.xlsx", "sheet_name": "供电",
        "row_index": 4, "cell_range": "B4:D4", "anchor": "供电!row 4",
        "raw_source_text": text, "raw_text": "合成前缀：市电两路 UPS容量500kVA",
        "source_text_hash": "sha256:" + hashlib.sha256(text.encode()).hexdigest(), "source_text_hash_space": "raw_source_text",
        "document_id": "doc1", "source_document_hash": "sha256:document-bytes", "index_version": "version-3", "point_id": "point1",
        "field_name": "UPS容量", "field_value": "500 kVA", "proof_attachment_ids": ["image1", "image2"],
        "proof_attachments": [
            {"attachment_id": "image1", "file_id": "doc1", "source_cell": "D4", "mapping_status": "mapped", "image_path": "/actual/image1.png"},
            {"attachment_id": "image2", "file_id": "doc1", "source_cell": "E4", "mapping_status": "image_unavailable"},
        ],
        "source": {"file_id": "doc1"}, "metadata": {"owner_only": True},
    }


def codes(errors: list[dict]) -> set[str]:
    return {error["code"] for error in errors}


def test_resolver_uses_only_hit_identity_and_keeps_full_original_text() -> None:
    source = hit()
    result = resolve_evidence_refs(["real-source", "real-source"], [source])

    assert result.resolvable and result.errors == [] and len(result.refs) == 1
    ref = result.refs[0]
    assert ref.source_text == source["raw_source_text"]
    assert ref.source_text != source["raw_text"]
    assert ref.file_name == source["file_name"]
    assert ref.knowledge_base_id == "kb1" and ref.namespace == "room301"
    assert ref.document_id == "doc1" and ref.index_version == "version-3"
    assert ref.quote is ref.start is ref.end is None
    assert validate_evidence_ref(ref, source) == []
    assert validate_evidence_ref(ref.to_dict(), source) == []
    source["proof_attachments"][0]["image_path"] = "/mutated.png"
    assert ref.proof_attachments[0]["image_path"] == "/actual/image1.png"


def test_unknown_ids_and_empty_selection_cannot_be_resolvable() -> None:
    source = hit()
    result = resolve_evidence_refs(["real-source", "invented"], [source])
    assert not result.resolvable
    assert len(result.refs) == 1
    assert codes(result.errors) == {"EV_REF_NOT_IN_RETRIEVAL"}
    assert result.errors[0]["chunk_id"] == "invented"
    assert not resolve_evidence_refs([], [source]).resolvable
    assert codes(validate_evidence_ref(result.refs[0], None)) == {"EV_REF_NOT_IN_RETRIEVAL"}


def test_native_source_without_declared_hash_uses_actual_unicode_hash() -> None:
    source = hit()
    source.pop("source_text_hash")
    source.pop("source_text_hash_space")
    result = resolve_evidence_refs(["real-source"], [source])

    assert result.resolvable
    assert result.refs[0].source_text_hash == "sha256:" + hashlib.sha256(source["raw_source_text"].encode()).hexdigest()
    assert result.refs[0].source_text_hash_space == "raw_source_text"
    assert validate_evidence_ref(result.refs[0], source) == []


@pytest.mark.parametrize(("hash_value", "space"), [("sha256:incorrect", "raw_source_text"), (None, "normalized_text")])
def test_bad_hash_or_declared_space_is_not_repaired(hash_value: str | None, space: str) -> None:
    source = hit()
    source.pop("source_text_hash")
    source.pop("source_text_hash_space")
    source["source"].update({"source_text_hash_space": space})
    if hash_value is not None:
        source["source"]["source_text_hash"] = hash_value
    result = resolve_evidence_refs(["real-source"], [source])

    assert not result.resolvable and result.refs == []
    assert codes(result.errors) == {"EV_REF_NOT_IN_RETRIEVAL"}
    assert source["source"]["source_text_hash_space"] == space


def test_legacy_raw_text_requires_matching_declared_hash_in_same_space() -> None:
    source = hit()
    source.pop("raw_source_text")
    source.pop("source_text_hash_space")
    source.pop("source_text_hash")

    unavailable = resolve_evidence_refs(["real-source"], [source])
    assert not unavailable.resolvable
    assert unavailable.errors[0]["reason"] == "native_source_text_unavailable"
    source["source_text_hash_space"] = "raw_text"
    source["source_text_hash"] = "sha256:" + hashlib.sha256(source["raw_text"].encode()).hexdigest()
    verified = resolve_evidence_refs(["real-source"], [source])
    assert verified.resolvable
    assert verified.refs[0].source_text == source["raw_text"]
    assert verified.refs[0].source_text_policy == "legacy_hash_verified"
    source["source_text_hash"] = "sha256:bad"
    assert not resolve_evidence_refs(["real-source"], [source]).resolvable


def test_good_top_hash_does_not_hide_a_bad_nested_hash_declaration() -> None:
    source = hit()
    source["source"]["source_text_hash"] = "sha256:bad-nested"

    result = resolve_evidence_refs(["real-source"], [source])

    assert not result.resolvable
    assert result.errors[0]["reason"] == "source_hash_mismatch"


def test_nested_native_text_preserves_source_hash_and_indices() -> None:
    source = {
        "chunk_id": "paragraph", "namespace": "room301", "file_name": "说明.docx", "source_type": "uploaded_docx_paragraph",
        "source": {"raw_source_text": "原文\r\n😀", "paragraph_index": 3, "index_version": "v5"},
    }
    result = resolve_evidence_refs(["paragraph"], [source])

    assert result.resolvable
    assert result.refs[0].source_text == "原文\r\n😀"
    assert result.refs[0].paragraph_index == 3
    assert result.refs[0].index_version == "v5"
    assert validate_evidence_ref(result.refs[0], source) == []


@pytest.mark.parametrize(("field_name", "forged_value", "code"), [
    ("cell_range", "Z999", "EV_REF_NOT_IN_RETRIEVAL"),
    ("sheet_name", "假Sheet", "EV_REF_NOT_IN_RETRIEVAL"),
    ("source_text", "模型写的内容", "EV_REF_NOT_IN_RETRIEVAL"),
    ("source_text_hash", "sha256:forged", "EV_REF_NOT_IN_RETRIEVAL"),
    ("source_text_hash_space", "raw_text", "EV_REF_NOT_IN_RETRIEVAL"),
    ("source_text_policy", "verified", "EV_REF_NOT_IN_RETRIEVAL"),
    ("index_version", "new-version", "EV_REF_NOT_IN_RETRIEVAL"),
    ("evidence_kind", "paragraph", "EV_REF_NOT_IN_RETRIEVAL"),
    ("file_name", "假来源.xlsx", "EV_FILE_NOT_IN_KB"),
    ("relative_path", "其他/来源.xlsx", "EV_FILE_NOT_IN_KB"),
    ("namespace", "other-room", "EV_FILE_NOT_IN_KB"),
    ("knowledge_base_id", "other-kb", "EV_FILE_NOT_IN_KB"),
    ("document_id", "other-doc", "EV_FILE_NOT_IN_KB"),
])
def test_stored_reference_tampering_fails_strict_comparison(field_name: str, forged_value: object, code: str) -> None:
    source = hit()
    ref = resolve_evidence_refs(["real-source"], [source]).refs[0].to_dict()
    ref[field_name] = forged_value
    assert code in codes(validate_evidence_ref(ref, source))


def test_self_asserted_verified_and_address_extras_are_not_ignored() -> None:
    source = hit()
    ref = resolve_evidence_refs(["real-source"], [source]).refs[0].to_dict()
    assert "EV_REF_NOT_IN_RETRIEVAL" in codes(validate_evidence_ref({**ref, "verified": True}, source))
    assert "EV_REF_NOT_IN_RETRIEVAL" in codes(validate_evidence_ref({**ref, "address": {"cell_range": "Z999"}}, source))
    assert "EV_REF_MISSING_ADDRESS" in codes(validate_evidence_ref({**ref, "row_index": True}, source))
    assert "EV_REF_MISSING_ADDRESS" in codes(validate_evidence_ref(replace(resolve_evidence_refs(["real-source"], [source]).refs[0], row_index=True), source))


@pytest.mark.parametrize("cell_range", ["A0", "ZZZ1", "XFE1", "A1048577", "D4:B4", "A5:A4", "能力!B4", "B4:WRONG", "B", "B4;C4"])
def test_invalid_excel_ranges_never_resolve(cell_range: str) -> None:
    source = {**hit(), "cell_range": cell_range}
    result = resolve_evidence_refs(["real-source"], [source])
    assert not result.resolvable
    assert "EV_CELL_RANGE_INVALID" in codes(result.errors)


def test_row_must_be_inside_declared_excel_cell_range() -> None:
    result = resolve_evidence_refs(["real-source"], [{**hit(), "row_index": 5}])
    assert not result.resolvable
    assert result.errors[0]["reason"] == "row_outside_cell_range"


def test_missing_file_or_physical_location_blocks_resolution() -> None:
    source = hit()
    no_file = {**source, "file_name": "", "relative_path": ""}
    assert "EV_FILE_NOT_IN_KB" in codes(resolve_evidence_refs(["real-source"], [no_file]).errors)
    no_address = {**source, "sheet_name": None, "cell_range": None, "row_index": None, "anchor": None}
    assert "EV_REF_MISSING_ADDRESS" in codes(resolve_evidence_refs(["real-source"], [no_address]).errors)
    blank_source = {**source, "raw_source_text": " \r\n "}
    assert not resolve_evidence_refs(["real-source"], [blank_source]).resolvable


def test_attachment_subsets_are_allowed_but_ids_and_image_metadata_cannot_be_forged() -> None:
    source = hit()
    ref = resolve_evidence_refs(["real-source"], [source]).refs[0]
    assert validate_evidence_ref(replace(ref, attachment_ids=["image1"], proof_attachments=[ref.proof_attachments[0]]), source) == []
    assert "EV_ATTACHMENT_NOT_FOUND" in codes(validate_evidence_ref(replace(ref, attachment_ids=["invented"]), source))
    forged = deepcopy(ref.proof_attachments)
    forged[0]["image_path"] = "/forged.png"
    assert "EV_ATTACHMENT_NOT_FOUND" in codes(validate_evidence_ref(replace(ref, proof_attachments=forged), source))


def test_unknown_kb_is_transparent_but_supplied_scope_is_strict() -> None:
    source = {**hit(), "knowledge_base_id": ""}
    result = resolve_evidence_refs(["real-source"], [source])
    assert result.resolvable and result.refs[0].knowledge_base_id == ""
    assert "EV_FILE_NOT_IN_KB" in codes(validate_evidence_ref(result.refs[0], source, known_knowledge_base_ids=["kb1"]))
    assert "EV_FILE_NOT_IN_KB" in codes(validate_evidence_ref(result.refs[0], source, known_knowledge_base_ids=[""]))
    source = hit()
    valid = resolve_evidence_refs(["real-source"], [source], known_knowledge_base_ids=["kb1"], knowledge_base_files={"kb1": ["知识库/能力清单.xlsx"]})
    assert valid.resolvable
    invalid = resolve_evidence_refs(["real-source"], [source], knowledge_base_files={"kb1": ["其他.xlsx"]})
    assert "EV_FILE_NOT_IN_KB" in codes(invalid.errors)


def test_scope_iterators_apply_to_every_resolved_reference() -> None:
    first, second = hit(), {**hit(), "chunk_id": "second", "evidence_id": "second"}
    result = resolve_evidence_refs(["real-source", "second"], [first, second], known_knowledge_base_ids=iter(["kb1"]), knowledge_base_files={"kb1": iter(["能力清单.xlsx"])})
    assert result.resolvable and len(result.refs) == 2


def test_duplicate_hit_scores_are_harmless_but_conflicting_origin_is_rejected() -> None:
    source = hit()
    copy = {**source, "vector_score": 0.1, "retrieval_layer": "target_text_detail", "metadata": {**source["metadata"], "vector_score": 0.9, "retrieval_layer": "target_text_detail"}}
    assert resolve_evidence_refs(["real-source"], [source, copy]).resolvable
    conflicting = {**source, "cell_range": "B5:D5", "row_index": 5}
    result = resolve_evidence_refs(["real-source"], [source, conflicting])
    assert not result.resolvable and result.refs == []
    assert result.errors[0]["reason"] == "conflicting_retrieval_hits"


def test_duplicate_valid_origin_cannot_hide_a_second_bad_hash_declaration() -> None:
    source = hit()
    broken = deepcopy(source)
    broken["source"]["source_text_hash"] = "sha256:invalid-nested-declaration"

    result = resolve_evidence_refs(["real-source"], [source, broken])

    assert not result.resolvable and result.refs == []
    assert "source_hash_mismatch" in {error["reason"] for error in result.errors}


def test_malformed_authority_payload_returns_diagnostics() -> None:
    source = hit()
    ref = resolve_evidence_refs(["real-source"], [source]).refs[0]
    broken = {**source, "metadata": "invalid-metadata"}

    assert validate_evidence_ref(ref, broken)[0]["reason"] == "invalid_retrieved_payload"
    result = resolve_evidence_refs(["real-source"], [broken])
    assert not result.resolvable
    assert result.errors[0]["reason"] == "invalid_retrieved_payload"


def test_embedded_table_preserves_parent_cell_separately_from_native_row() -> None:
    source = {
        "chunk_id": "embedded", "namespace": "room301", "file_name": "能力.xlsx", "source_type": "embedded_word_table",
        "sheet_name": "父表", "row_index": "E90", "anchor": "父表!E90 table 1", "raw_source_text": "UPS / 500 kVA",
        "parent_file_id": "parent", "embedded_file_name": "配置.docx", "source_chain": [{"file_id": "parent"}, {"file_id": "inside"}],
        "source": {"source_cell": "E90", "local_anchor": {"table_index": 1, "row_index": 2}, "source_row_indices": [2]},
    }
    result = resolve_evidence_refs(["embedded"], [source])

    assert result.resolvable
    ref = result.refs[0]
    assert ref.row_index == 2 and ref.table_index == 1
    assert ref.parent_source_cell == "E90"
    assert ref.local_anchor == {"table_index": 1, "row_index": 2}
    assert ref.source_chain == source["source_chain"]
    assert validate_evidence_ref(ref, source) == []
    legacy = {**source, "source": {"source_cell": "E90", "table_id": "real-table", "source_row_indices": [2]}}
    assert resolve_evidence_refs(["embedded"], [legacy]).refs[0].row_index == 2
    parent_only = {**source, "anchor": None, "source": {"source_cell": "E90"}}
    assert not resolve_evidence_refs(["embedded"], [parent_only]).resolvable


def test_text_chunk_and_legacy_cell_anchor_remain_addressable() -> None:
    text = {"chunk_id": "text", "namespace": "room301", "file_name": "记录.txt", "evidence_kind": "document_chunk", "anchor": "text chunk 2", "raw_source_text": "现场原文"}
    assert resolve_evidence_refs(["text"], [text]).resolvable
    excel = {"chunk_id": "excel", "file_name": "能力.xlsx", "source_type": "main_excel_capability", "anchor": "'供 电'!B4", "raw_source_text": "市电 / 两路"}
    ref = resolve_evidence_refs(["excel"], [excel]).refs[0]
    assert ref.sheet_name == "供 电" and ref.cell_range == "B4"


def test_optional_quote_spans_use_original_unicode_and_crlf_offsets() -> None:
    source = hit()
    ref = resolve_evidence_refs(["real-source"], [source]).refs[0]
    quote = "\r\n\tUPS容量 / 500 kVA"
    start = source["raw_source_text"].index(quote)
    cited = replace(ref, quote=quote, start=start, end=start + len(quote))

    assert validate_evidence_ref(cited, source) == []
    assert "EV_REF_NOT_IN_RETRIEVAL" in codes(validate_evidence_ref(replace(cited, start=start + 1), source))
    assert "EV_REF_NOT_IN_RETRIEVAL" in codes(validate_evidence_ref(replace(cited, quote=quote.replace("\r\n", "\n")), source))
