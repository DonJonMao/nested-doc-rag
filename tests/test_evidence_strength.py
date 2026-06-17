from __future__ import annotations

from nested_doc_rag.grounding import EvidenceStrengthEvaluator, strength_rank
from nested_doc_rag.schemas.eval import FieldPrediction


def test_answered_without_sources_is_e0() -> None:
    result = evaluator().evaluate(item=item("UPS容量"), prediction=prediction("500kVA", []), top_hits=hits())

    assert result.evidence_strength == "E0"
    assert "no_source_chunk_ids" in result.reasons


def test_answered_value_missing_from_evidence_is_not_strong() -> None:
    result = evaluator().evaluate(item=item("UPS容量"), prediction=prediction("500kVA", ["chunk_main"]), top_hits=hits(raw_text="UPS容量：300kVA。"))

    assert strength_rank(result.evidence_strength) < strength_rank("E3")
    assert "missing_numeric_or_unit_support" in result.reasons


def test_numeric_answer_with_same_number_and_unit_is_strong() -> None:
    result = evaluator().evaluate(item=item("UPS容量"), prediction=prediction("500kVA", ["chunk_main"]), top_hits=hits(raw_text="UPS容量：500kVA。"))

    assert result.evidence_strength in {"E3", "E4"}
    assert "500kva" in result.matched_answer_tokens


def test_global_intro_alone_cannot_strongly_support_answer_by_default() -> None:
    result = evaluator().evaluate(
        item=item("UPS容量"),
        prediction=prediction("500kVA", ["chunk_global"]),
        top_hits=[
            {
                "chunk_id": "chunk_global",
                "namespace": "global",
                "source_type": "intro_doc_paragraph",
                "corpus_layer": "intro_doc",
                "retrieval_layer": "global_intro",
                "raw_text": "园区UPS容量为500kVA。",
                "text_for_embedding": "园区UPS容量为500kVA。",
            }
        ],
    )

    assert strength_rank(result.evidence_strength) < strength_rank("E3")
    assert "global_intro_only" in result.reasons


def test_target_main_fact_supports_exact_structured_answer() -> None:
    result = evaluator().evaluate(item=item("UPS容量"), prediction=prediction("500kVA", ["chunk_main"]), top_hits=hits(raw_text="UPS容量：500kVA。"))

    assert result.evidence_strength == "E4"


def test_partial_clue_is_not_promoted_to_answered() -> None:
    pred = prediction("未找到", [], status="partial_clue")

    result = evaluator().evaluate(item=item("UPS容量"), prediction=pred, top_hits=hits())

    assert pred.answer_status == "partial_clue"
    assert "partial_clue_status_preserved" in result.reasons


def evaluator() -> EvidenceStrengthEvaluator:
    return EvidenceStrengthEvaluator(target_namespace="xixian_4")


def item(question_text: str) -> dict:
    return {"form_item_id": "field_1", "row_index": 1, "target_cell": "D1", "question_text": question_text}


def prediction(answer_value: str, sources: list[str], *, status: str = "answered") -> FieldPrediction:
    return FieldPrediction(
        field_id="field_1",
        row_index=1,
        target_cell="D1",
        answer_value=answer_value,
        answer_status=status,
        confidence=0.9,
        source_chunk_ids=sources,
        method_name="test",
    )


def hits(raw_text: str = "UPS容量：500kVA。") -> list[dict]:
    return [
        {
            "chunk_id": "chunk_main",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "corpus_layer": "fact",
            "retrieval_layer": "target_main_fact",
            "raw_text": raw_text,
            "text_for_embedding": raw_text,
        }
    ]
