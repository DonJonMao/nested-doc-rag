from __future__ import annotations

import hashlib

import pytest

from nested_doc_rag.evidence_record import EvidenceRecord, normalize_evidence_record


@pytest.mark.parametrize(("source_type", "segment_type", "kind"), [
    ("main_excel_capability", None, "structured_field"),
    ("embedded_word_table", None, "table_row"),
    ("embedded_raw_segment", "embedded_docx_paragraph", "paragraph"),
    ("embedded_raw_segment", "embedded_doc_paragraph", "paragraph"),
    ("embedded_raw_segment", "embedded_docx_table_row", "table_row"),
    ("embedded_raw_segment", "embedded_xlsx_row", "table_row"),
    ("embedded_raw_segment", "unrecognized", "document_chunk"),
    ("intro_doc_paragraph", None, "document_intro"),
    ("intro_doc_table_row", None, "table_row"),
    ("uploaded_excel_row", None, "table_row"),
    ("uploaded_docx_paragraph", None, "paragraph"),
    ("uploaded_text_chunk", None, "document_chunk"),
])
def test_legacy_kinds_preserve_source_identity(source_type: str, segment_type: str | None, kind: str) -> None:
    payload = {"chunk_id": "original-id", "source_type": source_type, "source": {"segment_type": segment_type}}

    record = normalize_evidence_record(payload)

    assert record["evidence_kind"] == kind
    assert record["source_type"] == source_type
    assert record["chunk_id"] == record["evidence_id"] == "original-id"
    assert record["metadata"]["original_source_type"] == source_type
    assert "raw_source_text" not in record
    assert "source_text_hash" not in record


def test_normalization_is_lossless_and_idempotent() -> None:
    payload = {
        "chunk_id": "legacy-id", "point_id": "stable-point", "source_type": "embedded_word_table",
        "namespace": "xixian_4", "corpus_layer": "template", "embedding_policy": "embed_as_template",
        "rank_boost": 1.04, "default_index": False, "raw_text": "normalized content",
        "raw_source_text": "native\r\n  内容😀", "row_index": "E90",
        "source_chain": [{"parent_file_id": "parent"}, {"file_id": "embedded"}],
        "parent_attachment_id": "att1", "proof_attachment_ids": ["att1"],
        "proof_attachments": [{"attachment_id": "att1", "mapping_status": "mapped", "image_path": "/image.png"}],
        "source": {"file_name": "inside.docx", "local_anchor": {"table_index": 3, "row_index": 2}},
        "custom_flag": {"owner_only": True},
    }

    record = normalize_evidence_record(payload)

    assert normalize_evidence_record(record) == record
    assert record["row_index"] == "E90"
    assert record["address"]["row_index"] == 2
    assert record["address"]["table_index"] == 3
    for key in ("point_id", "source_chain", "parent_attachment_id", "proof_attachments", "embedding_policy", "rank_boost", "default_index", "custom_flag"):
        assert record[key] == payload[key]
    assert record["source_text_hash"] == "sha256:" + hashlib.sha256(payload["raw_source_text"].encode()).hexdigest()
    assert record["source_text_hash_space"] == "raw_source_text"
    assert EvidenceRecord.from_legacy(record).evidence_id == "legacy-id"


@pytest.mark.parametrize(("name_key", "value"), [("capability_desc", 0), ("question_text", False)])
def test_main_fields_use_declared_metadata_and_retain_zero(name_key: str, value: object) -> None:
    record = normalize_evidence_record({
        "chunk_id": "main", "source_type": "main_excel_capability",
        name_key: "油机数量", "answer_value": value, "raw_text": "irrelevant normalized text",
    })

    assert record["field_name"] == "油机数量"
    assert record["field_value"] == str(value)
    assert record[name_key] == "油机数量"
    assert record["answer_value"] == value
    unknown = normalize_evidence_record({"chunk_id": "missing", "source_type": "main_excel_capability", "raw_text": "油机数量 / 2"})
    assert unknown["field_name"] is unknown["field_value"] is None


def test_existing_hash_declarations_are_not_repaired_silently() -> None:
    record = normalize_evidence_record({
        "chunk_id": "broken", "raw_text": "compact", "raw_source_text": "actual original",
        "source": {"source_text_hash": "sha256:wrong", "source_text_hash_space": "normalized_text"},
    })

    assert record["source_text_hash"] == "sha256:wrong"
    assert record["source_text_hash_space"] == "normalized_text"


def test_location_adapters_distinguish_parent_cells_and_native_document_indices() -> None:
    embedded = normalize_evidence_record({
        "chunk_id": "embedded", "source_type": "embedded_word_table", "row_index": "E90",
        "source": {"row_index": 4, "table_index": 2, "source_cell": "E90"},
    })
    intro = normalize_evidence_record({"chunk_id": "intro", "source_type": "intro_doc_paragraph", "row_index": 7})
    typed_anchor = normalize_evidence_record({
        "chunk_id": "typed", "source_type": "main_excel_capability", "anchor": "能力!row 2",
        "source_anchor": {"sheet_name": "能力", "row_index": 2, "cell_range": "B2:D2"},
    })

    assert embedded["row_index"] == "E90"
    assert embedded["address"]["row_index"] == 4
    assert intro["address"]["paragraph_index"] == 7
    assert intro["address"]["row_index"] is None
    assert normalize_evidence_record(intro) == intro
    assert typed_anchor["address"]["cell_range"] == "B2:D2"
    assert typed_anchor["address"]["source_anchor"] == "能力!row 2"


def test_canonical_address_precedes_legacy_local_anchor() -> None:
    record = normalize_evidence_record({
        "chunk_id": "canonical", "source_type": "embedded_word_table",
        "address": {"table_index": 1, "row_index": 2},
        "source": {"local_anchor": {"table_index": 3, "row_index": 4}},
    })

    assert record["address"]["table_index"] == 1
    assert record["address"]["row_index"] == 2
    assert normalize_evidence_record(record) == record


def test_native_anchor_fallback_and_invalid_kind() -> None:
    record = normalize_evidence_record({"chunk_id": "row", "anchor": "table 2 row 4"})
    assert record["address"]["table_index"] == 2
    assert record["address"]["row_index"] == 4
    with pytest.raises(ValueError, match="unsupported evidence_kind"):
        normalize_evidence_record({"chunk_id": "bad", "evidence_kind": "invented"})
