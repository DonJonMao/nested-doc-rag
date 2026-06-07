from __future__ import annotations

from nested_doc_rag.agent.policies import build_query_plan, make_prediction_from_evidence, select_evidence
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction


def make_field(field_id: str, question_text: str, field_type: str = "text") -> FieldGold:
    return FieldGold.from_dict(
        {
            "field_id": field_id,
            "row_index": 1,
            "target_cell": "C1",
            "question_text": question_text,
            "expected_value": "SHOULD_NOT_BE_USED",
            "field_type": field_type,
            "required": True,
            "must_have_evidence": True,
        }
    )


def test_reference_only_outputs_partial_clue() -> None:
    field = make_field("field_ups", "UPS容量", "number")
    plan = build_query_plan(field, target_namespace="xixian_4")
    bundle = select_evidence(
        [
            {
                "chunk_id": "global_ups",
                "namespace": "global",
                "source_type": "intro_doc_paragraph",
                "corpus_layer": "intro_doc",
                "retrieval_layer": "global_intro",
                "raw_text": "园区介绍中提到 UPS 容量口径需要结合目标机房表格确认。",
            }
        ],
        field,
        plan,
    )
    prediction = make_prediction_from_evidence(field, bundle)

    assert bundle.selected_chunks == []
    assert bundle.reference_chunks[0]["chunk_id"] == "global_ups"
    assert prediction.answer_status == "partial_clue"
    assert prediction.reference_chunk_ids == ["global_ups"]
    assert prediction.reference_source_documents[0]["chunk_id"] == "global_ups"
    assert prediction.source_chunk_ids == []


def test_direct_evidence_outputs_answered() -> None:
    field = make_field("field_power", "是否满足双路供电", "enum")
    plan = build_query_plan(field, target_namespace="xixian_4")
    bundle = select_evidence(
        [
            {
                "chunk_id": "target_power",
                "namespace": "xixian_4",
                "source_type": "main_excel_capability",
                "corpus_layer": "fact",
                "retrieval_layer": "target_main_fact",
                "field_id": "field_power",
                "answer_value": "满足",
                "answer_status": "answered",
                "source_chunk_ids": ["target_power"],
                "raw_text": "是否满足双路供电：满足。",
            }
        ],
        field,
        plan,
    )
    prediction = make_prediction_from_evidence(field, bundle)

    assert bundle.selected_chunks[0]["chunk_id"] == "target_power"
    assert prediction.answer_status == "answered"
    assert prediction.source_chunk_ids == ["target_power"]


def test_global_intro_not_direct_for_capacity_field() -> None:
    field = make_field("field_oil", "油机发电容量", "number")
    plan = build_query_plan(field, target_namespace="xixian_4")
    bundle = select_evidence(
        [
            {
                "chunk_id": "global_oil",
                "namespace": "global",
                "source_type": "intro_doc_paragraph",
                "corpus_layer": "intro_doc",
                "retrieval_layer": "global_intro",
                "answer_value": "800 kW",
                "raw_text": "园区介绍：油机发电容量示例为 800 kW。",
            }
        ],
        field,
        plan,
    )
    prediction = make_prediction_from_evidence(field, bundle)

    assert bundle.decision == "clue_only"
    assert prediction.answer_status == "partial_clue"
    assert prediction.source_chunk_ids == []


def test_global_policy_can_be_direct_for_security_policy() -> None:
    field = make_field("field_photo", "机房是否禁止拍照", "bool")
    plan = build_query_plan(field, target_namespace="xixian_4")
    bundle = select_evidence(
        [
            {
                "chunk_id": "global_policy",
                "namespace": "global",
                "source_type": "intro_doc_paragraph",
                "corpus_layer": "intro_doc",
                "retrieval_layer": "global_intro",
                "answer_value": "是",
                "raw_text": "机房管理制度：进入机房后禁止拍照，拍照需单独审批。",
            }
        ],
        field,
        plan,
    )
    prediction = make_prediction_from_evidence(field, bundle)

    assert bundle.decision == "use_direct_evidence"
    assert prediction.answer_status == "answered"
    assert prediction.answer_value == "是"


def test_liquid_cooling_scope_mismatch() -> None:
    field = make_field("field_liquid", "是否支持液冷机柜", "bool")
    plan = build_query_plan(field, target_namespace="xixian_4")
    bundle = select_evidence(
        [
            {
                "chunk_id": "target_power_only",
                "namespace": "xixian_4",
                "source_type": "main_excel_capability",
                "corpus_layer": "fact",
                "retrieval_layer": "target_main_fact",
                "answer_value": "是",
                "raw_text": "机柜供电：支持双路 U 电，空开容量满足。",
            }
        ],
        field,
        plan,
    )
    prediction = make_prediction_from_evidence(field, bundle)

    assert bundle.selected_chunks == []
    assert prediction.answer_status != "answered"


def test_not_applicable_direct_answer() -> None:
    field = make_field("field_liquid_na", "是否涉及液冷改造", "bool")
    plan = build_query_plan(field, target_namespace="xixian_4")
    bundle = select_evidence(
        [
            {
                "chunk_id": "target_na",
                "namespace": "xixian_4",
                "source_type": "main_excel_capability",
                "corpus_layer": "fact",
                "retrieval_layer": "target_main_fact",
                "field_id": "field_liquid_na",
                "answer_value": "不涉及",
                "answer_status": "answered",
                "source_chunk_ids": ["target_na"],
                "raw_text": "是否涉及液冷改造：不涉及。",
            }
        ],
        field,
        plan,
    )
    prediction = make_prediction_from_evidence(field, bundle)

    assert prediction.answer_status == "answered"
    assert "不涉及" in str(prediction.answer_value)


def test_prediction_reference_fields_backward_compatible() -> None:
    prediction = FieldPrediction.from_dict(
        {
            "field_id": "old",
            "row_index": 1,
            "target_cell": "C1",
            "answer_value": "旧答案",
            "answer_status": "answered",
        }
    )

    assert prediction.reference_chunk_ids == []
    assert prediction.reference_source_documents == []
    assert prediction.to_dict()["reference_snippets"] == []
