from __future__ import annotations

import hashlib
from array import array

from nested_doc_rag.embedding import manifest


def test_main_manifest_has_declared_field_values_and_native_source() -> None:
    original = "机房名称\r\n  西咸4号楼😀  "
    record = manifest.make_main_excel_manifest_record({
        "segment_id": "main", "data_center_id": "xixian_4", "file_id": "file1",
        "file_name": "能力.xlsx", "relative_path": "目录/能力.xlsx", "sheet_name": "动力", "row_index": 2,
        "source_anchor": {"cell_range": "B2:E2"}, "capability_desc": "机房名称", "answer_value": 0,
        "category_path": ["动力", "供电"], "raw_text": original,
    })

    assert record["evidence_kind"] == "structured_field"
    assert record["field_name"] == "机房名称"
    assert record["field_value"] == "0"
    assert record["raw_text"] == "机房名称 西咸4号楼😀"
    assert record["raw_source_text"] == original
    assert record["source_text_hash"] == "sha256:" + hashlib.sha256(original.encode()).hexdigest()
    assert record["address"]["cell_range"] == "B2:E2"
    assert record["structural_path"] == ["动力", "供电"]
    assert record["source"]["file_id"] == "file1"


def test_embedded_manifest_preserves_policy_parent_cell_and_source_chain() -> None:
    source_chain = [{"file_id": "parent"}, {"file_id": "inside"}]
    segment = {
        "segment_id": "embedded", "data_center_id": "xixian_4", "file_name": "现场.docx",
        "parent_sheet_name": "能力", "parent_source_cell": "E90", "parent_attachment_id": "image1",
        "parent_segment_id": "parent-seg", "parent_file_id": "parent-file", "embedded_object_id": "object1",
        "local_anchor": {"table_index": 2, "row_index": 3}, "source_chain": source_chain,
        "raw_text": "\n  UPS / 2400 kVA\n", "source_text_hash": "sha256:declared-invalid",
        "source_text_hash_space": "legacy_space", "context": "动力", "group": "UPS",
        "proof_attachments": [{"attachment_id": "image1", "source_cell": "E90", "mapping_status": "image_unavailable"}],
    }
    record = manifest.make_embedded_table_manifest_record(segment, {"embedding_policy": "embed_as_template"})

    assert record["evidence_kind"] == "table_row"
    assert record["corpus_layer"] == "template"
    assert record["default_index"] is False
    assert record["embedding_policy"] == "embed_as_template"
    assert record["row_index"] == "E90"
    assert record["address"]["row_index"] == 3
    assert record["address"]["table_index"] == 2
    assert record["parent_chunk_id"] == "parent-seg"
    assert record["source_chain"] == source_chain
    assert record["source"]["parent_file_id"] == "parent-file"
    assert record["source"]["embedded_object_id"] == "object1"
    assert record["source_text_hash"] == "sha256:declared-invalid"
    assert record["source_text_hash_space"] == "legacy_space"
    assert record["proof_attachments"][0]["mapping_status"] == "image_unavailable"
    assert record["field_name"] is record["field_value"] is None


def test_local_index_search_preserves_canonical_evidence_and_provenance(monkeypatch) -> None:
    original = "供电路数\n  2"
    record = manifest.make_main_excel_manifest_record({
        "segment_id": "main", "data_center_id": "xixian_4", "raw_text": original,
        "capability_desc": "供电路数", "answer_value": "2", "file_name": "清单.xlsx",
        "source_anchor": {"cell_range": "A2:B2"}, "row_index": 2,
    })
    monkeypatch.setattr(manifest, "load_index", lambda _: ({"dimension": 2}, [record], array("f", [1, 0])))

    class FakeEmbedder:
        def __init__(self, **kwargs):
            pass

        def embed_query(self, query):
            return [1, 0]

    monkeypatch.setattr(manifest, "EmbeddingClient", FakeEmbedder)

    hit = manifest.search_index("供电路数")[0]

    for key in ("evidence_id", "point_id", "address", "raw_source_text", "source_text_hash", "metadata", "source", "field_name", "field_value"):
        assert hit[key] == record[key]
    assert hit["vector_rank"] == 1
    assert hit["vector_score"] == 1.0


def test_manifest_preserves_nested_native_text_and_hash_declarations() -> None:
    record = manifest.make_embedded_table_manifest_record({
        "segment_id": "nested", "raw_text": "already normalized", "parent_source_cell": "E90",
        "source": {
            "raw_source_text": "original\r\n  whitespace", "source_text_hash": "sha256:bad-declaration",
            "source_text_hash_space": "normalized", "table_index": 3, "row_index": 4,
        },
    }, {"embedding_policy": "embed"})

    assert record["raw_source_text"] == "original\r\n  whitespace"
    assert record["source_text_hash"] == "sha256:bad-declaration"
    assert record["source_text_hash_space"] == "normalized"
    assert record["address"]["table_index"] == 3
    assert record["address"]["row_index"] == 4
    assert record["row_index"] == "E90"
