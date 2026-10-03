from __future__ import annotations

import pytest

from nested_doc_rag.schemas.eval import FieldPrediction
from nested_doc_rag.schemas.evidence import EvidenceRef


def ref_data() -> dict:
    return {
        "chunk_id": "source1", "knowledge_base_id": "kb1", "namespace": "room301",
        "file_name": "能力.xlsx", "relative_path": "目录/能力.xlsx", "evidence_kind": "structured_field",
        "sheet_name": "动力", "row_index": 4, "cell_range": "B4:C4", "source_text": "UPS容量 / 500kVA",
        "source_text_hash": "sha256:actual", "attachment_ids": ["image1"],
        "proof_attachments": [{"attachment_id": "image1", "mapping_status": "mapped", "image_path": "/source.png"}],
        "source_chain": [{"file_id": "parent"}, {"file_id": "inside"}], "local_anchor": {"table_index": 2},
    }


def test_evidence_ref_json_roundtrip_preserves_source_identity_and_does_not_alias() -> None:
    source = ref_data()
    ref = EvidenceRef.from_dict(source)
    serialized = ref.to_dict()

    assert EvidenceRef.from_dict(serialized) == ref
    assert ref.evidence_id == "source1"
    serialized["proof_attachments"][0]["image_path"] = "/forged.png"
    source["source_chain"][0]["file_id"] = "changed"
    assert ref.proof_attachments[0]["image_path"] == "/source.png"
    assert ref.source_chain[0]["file_id"] == "parent"


def test_field_prediction_keeps_typed_refs_and_legacy_fields() -> None:
    data = {
        "field_id": "form1", "row_index": 2, "target_cell": "'动力'!D2", "answer_value": "500kVA",
        "source_chunk_ids": ["source1"], "evidence_attachment_ids": ["image1"],
        "reference_source_documents": [{"chunk_id": "source1", "quote": "500kVA"}], "evidence_refs": [ref_data()],
    }
    prediction = FieldPrediction.from_dict(data)

    assert isinstance(prediction.evidence_refs[0], EvidenceRef)
    serialized = prediction.to_dict()
    assert FieldPrediction.from_dict(serialized) == prediction
    for name in ("source_chunk_ids", "evidence_attachment_ids", "reference_source_documents"):
        assert serialized[name] == data[name]
    legacy = FieldPrediction.from_dict({key: value for key, value in data.items() if key != "evidence_refs"})
    assert legacy.evidence_refs == []
    # Existing callers passing positional compatibility fields keep their slots.
    positional = FieldPrediction("form2", 3, "D3", "v", "answered", 0.9, ["s"], ["a"], ["reference"])
    assert positional.reference_chunk_ids == ["reference"]
    assert positional.evidence_refs == []


@pytest.mark.parametrize(("name", "value"), [
    ("row_index", True), ("row_index", "E90"), ("row_index", 0),
    ("paragraph_index", -1), ("table_index", 2.5),
    ("start", -1), ("end", "3"), ("start", False),
    ("source_chain", ["not-object"]), ("proof_attachments", "not-list"),
    ("local_anchor", "not-object"), ("attachment_ids", "not-list"),
    ("source_row_indices", [None]),
    ("attachment_ids", False), ("proof_attachments", False), ("metadata", False), ("local_anchor", False),
])
def test_evidence_ref_rejects_malformed_types(name: str, value: object) -> None:
    with pytest.raises(ValueError):
        EvidenceRef.from_dict({**ref_data(), name: value})


def test_unknown_stored_fields_are_retained_for_strict_validation() -> None:
    ref = EvidenceRef.from_dict({**ref_data(), "verified": True, "address": {"cell_range": "Z999"}})
    assert ref.metadata["verified"] is True
    assert ref.metadata["address"] == {"cell_range": "Z999"}
