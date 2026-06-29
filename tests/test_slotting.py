from __future__ import annotations

from nested_doc_rag.agent.slotting import SlotDecomposition, SlotSpec, evaluate_slot_consistency
from nested_doc_rag.schemas.eval import FieldPrediction


def test_allowed_values_are_soft_hints_not_answer_whitelist() -> None:
    decomposition = SlotDecomposition(
        is_composite=True,
        slots=[
            SlotSpec.from_dict(
                {
                    "name": "pressure_type",
                    "label": "高压或低压",
                    "allowed_values": ["高压", "低压"],
                    "closed_set": True,
                }
            )
        ],
    )
    prediction = FieldPrediction(
        field_id="field_chiller",
        row_index=53,
        target_cell="D53",
        answer_value="10kV高压离心式",
        answer_status="answered",
        confidence=0.9,
        source_chunk_ids=["chunk_chiller"],
    )
    generated = {
        "slot_values": [
            {
                "name": "pressure_type",
                "raw_value": "10kV高压离心式",
                "normalized_value": "高压",
                "source_chunk_ids": ["chunk_chiller"],
            }
        ]
    }
    top_hits = [
        {
            "chunk_id": "chunk_chiller",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "raw_text": "冷水机组配置：10kV高压离心式冷水机组。",
        }
    ]

    result = evaluate_slot_consistency(item={}, prediction=prediction, generated=generated, top_hits=top_hits, decomposition=decomposition)

    assert result.passed is True
    assert result.unsupported_slots == []


def test_canonical_hints_are_soft_and_raw_evidence_value_is_allowed() -> None:
    decomposition = SlotDecomposition(
        is_composite=True,
        slots=[
            SlotSpec(
                name="pressure_type",
                label="高压或低压",
                canonical_hints=["高压", "低压"],
                closed_set=False,
                allow_evidence_value=True,
            )
        ],
    )
    prediction = FieldPrediction(
        field_id="field_chiller",
        row_index=53,
        target_cell="D53",
        answer_value="10kV高压离心式",
        answer_status="answered",
        confidence=0.9,
        source_chunk_ids=["chunk_chiller"],
    )
    generated = {
        "slot_values": [
            {
                "name": "pressure_type",
                "raw_value": "10kV高压离心式",
                "normalized_value": "高压",
                "source_chunk_ids": ["chunk_chiller"],
            }
        ]
    }
    top_hits = [
        {
            "chunk_id": "chunk_chiller",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "raw_text": "冷水机组配置：10kV高压离心式冷水机组。",
        }
    ]

    result = evaluate_slot_consistency(item={}, prediction=prediction, generated=generated, top_hits=top_hits, decomposition=decomposition)

    assert result.passed is True
    assert result.unsupported_slots == []


def test_canonical_hints_do_not_substitute_for_conflicting_evidence() -> None:
    decomposition = SlotDecomposition(
        is_composite=True,
        slots=[
            SlotSpec(
                name="pressure_type",
                label="高压或低压",
                canonical_hints=["高压", "低压"],
                closed_set=False,
                allow_evidence_value=True,
            )
        ],
    )
    prediction = FieldPrediction(
        field_id="field_chiller",
        row_index=53,
        target_cell="D53",
        answer_value="高压",
        answer_status="answered",
        confidence=0.9,
        source_chunk_ids=["chunk_chiller"],
    )
    generated = {
        "slot_values": [
            {
                "name": "pressure_type",
                "raw_value": "高压",
                "normalized_value": "高压",
                "source_chunk_ids": ["chunk_chiller"],
            }
        ]
    }
    top_hits = [
        {
            "chunk_id": "chunk_chiller",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "raw_text": "冷水机组配置：低压冷水机组。",
        }
    ]

    result = evaluate_slot_consistency(item={}, prediction=prediction, generated=generated, top_hits=top_hits, decomposition=decomposition)

    assert result.passed is False
    assert result.unsupported_slots == ["pressure_type"]
