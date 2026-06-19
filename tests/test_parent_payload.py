from __future__ import annotations

from nested_doc_rag.evaluation.step15_engine import build_qdrant_answer_messages
from nested_doc_rag.grounding import EvidenceStrengthEvaluator
from nested_doc_rag.retrieval.parent_payload import attach_parent_payloads, build_parent_payload
from nested_doc_rag.schemas.eval import FieldPrediction


def test_attach_parent_payload_preserves_hit_order() -> None:
    hits = [hit("a", vector_rank=1), hit("b", vector_rank=2), hit("c", vector_rank=3)]

    enriched = attach_parent_payloads(hits)

    assert [item["chunk_id"] for item in enriched] == ["a", "b", "c"]
    assert [item["vector_rank"] for item in enriched] == [1, 2, 3]


def test_parent_payload_from_existing_metadata() -> None:
    payload = build_parent_payload(
        hit(
            "chunk_1",
            sheet_name="设备清单",
            table_title="UPS系统",
            section_path=["供电", "UPS"],
            row_header="UPS",
            column_header="容量",
            unit="kVA",
            source_document="main.xlsx",
            row_index=12,
            cell_range="D12",
        )
    )

    assert payload.sheet_name == "设备清单"
    assert payload.table_title == "UPS系统"
    assert payload.section_path == "供电 / UPS"
    assert payload.row_header == "UPS"
    assert payload.column_header == "容量"
    assert payload.unit == "kVA"
    assert payload.row_index == 12
    assert payload.cell_range == "D12"
    assert payload.confidence == "high"


def test_parent_payload_from_raw_text_prefixes() -> None:
    payload = build_parent_payload(
        hit(
            "chunk_1",
            raw_text="类别: UPS系统；能力描述: 容量；单位: kVA；现状: 500kVA；机房: 301机房",
        )
    )

    assert payload.row_header == "UPS系统"
    assert payload.column_header == "容量"
    assert payload.unit == "kVA"
    assert payload.status == "current"
    assert payload.scope == "301机房"


def test_missing_parent_payload_is_safe() -> None:
    payload = build_parent_payload({"chunk_id": "missing"})

    assert payload.confidence == "missing"
    assert "no_parent_metadata" in payload.reasons


def test_parent_payload_max_chars() -> None:
    payload = build_parent_payload(hit("chunk_1", parent_text="x" * 1000), max_chars=40)

    assert payload.parent_text is not None
    assert len(payload.parent_text) <= 40


def test_prompt_includes_parent_payload() -> None:
    enriched = attach_parent_payloads(
        [
            hit(
                "chunk_1",
                source_document="main.xlsx",
                sheet_name="设备清单",
                table_title="UPS系统",
                row_header="UPS",
                column_header="容量",
                unit="kVA",
                raw_text="UPS容量：500kVA。",
            )
        ]
    )

    messages = build_qdrant_answer_messages(form_item(), "UPS容量", enriched, room_context="西咸4号楼 301机房")
    prompt = messages[-1]["content"]
    assert "结构路径" in prompt
    assert "字段路径" in prompt
    assert "单位" in prompt
    assert "kVA" in prompt


def test_grounding_uses_parent_payload_for_parent_exact() -> None:
    enriched = attach_parent_payloads(
        [
            hit(
                "chunk_1",
                raw_text="500",
                parent_payload={
                    "sheet_name": "设备清单",
                    "table_title": "UPS系统",
                    "row_header": "UPS",
                    "column_header": "UPS容量",
                    "unit": "kVA",
                    "scope": "target",
                    "status": "current",
                    "parent_text": "UPS系统 / UPS容量 / 单位 kVA / 301机房",
                    "confidence": "high",
                    "reasons": [],
                },
            )
        ]
    )
    prediction = FieldPrediction(
        field_id="field_1",
        row_index=1,
        target_cell="D1",
        answer_value="500kVA",
        answer_status="answered",
        confidence=0.9,
        source_chunk_ids=["chunk_1"],
        method_name="test",
    )

    result = EvidenceStrengthEvaluator(target_namespace="xixian_4", room_context="西咸4号楼 301机房").evaluate(
        item=form_item(question_text="301机房 UPS 容量是多少？"),
        prediction=prediction,
        top_hits=enriched,
    )

    assert result.field_binding == "parent_exact"


def test_parent_payload_does_not_change_retrieval_rank() -> None:
    original = hit("chunk_1", vector_rank=7, vector_score=0.88, rerank_rank=2, rerank_score=0.91, final_rank=1)

    enriched = attach_parent_payloads([original])[0]

    assert enriched["chunk_id"] == original["chunk_id"]
    assert enriched["vector_rank"] == original["vector_rank"]
    assert enriched["vector_score"] == original["vector_score"]
    assert enriched["rerank_rank"] == original["rerank_rank"]
    assert enriched["rerank_score"] == original["rerank_score"]
    assert enriched["final_rank"] == original["final_rank"]


def hit(chunk_id: str, **overrides):
    record = {
        "chunk_id": chunk_id,
        "namespace": "xixian_4",
        "source_type": "main_excel_capability",
        "corpus_layer": "fact",
        "retrieval_layer": "target_main_fact",
        "raw_text": overrides.get("raw_text", "UPS容量：500kVA。"),
        "text_for_embedding": overrides.get("text_for_embedding", overrides.get("raw_text", "UPS容量：500kVA。")),
        "vector_rank": overrides.get("vector_rank", 1),
        "vector_score": overrides.get("vector_score", 0.9),
    }
    record.update(overrides)
    return record


def form_item(question_text: str = "UPS容量是多少？") -> dict:
    return {
        "form_item_id": "field_1",
        "file_name": "main.xlsx",
        "sheet_name": "Sheet1",
        "target_cell": "D1",
        "row_index": 1,
        "category_path": ["供电", "UPS"],
        "question_text": question_text,
        "instruction_text": "填写UPS容量",
        "answer_example": "500kVA",
        "needs_evidence": True,
    }
