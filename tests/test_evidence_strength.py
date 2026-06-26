from __future__ import annotations

from nested_doc_rag.agent.step15_runner import AgentOverlay
from nested_doc_rag.grounding import EvidenceStrengthEvaluator, EvidenceStrengthResult, apply_evidence_strength_to_overlay, strength_rank
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


def test_apply_evidence_strength_only_makes_overlay_more_conservative() -> None:
    pred = prediction("500kVA", ["chunk_missing"])
    overlay = overlay_row(writeback_allowed=True, review_required=False, risk_level="low")
    e0 = EvidenceStrengthResult("E0", ["no_valid_evidence_support"], [], ["chunk_missing"], [], [], ["500kva"])

    blocked = apply_evidence_strength_to_overlay(
        pred,
        overlay,
        e0,
        min_strength_for_answered="E3",
        min_strength_for_writeback="E3",
        downgrade_unsupported_answer_to_partial=False,
    )

    assert blocked.writeback_allowed is False
    assert blocked.review_required is True
    assert blocked.risk_level == "high"
    assert "no_valid_evidence_support" in blocked.reasons

    already_blocked = overlay_row(writeback_allowed=False, review_required=True, risk_level="medium")
    strong = EvidenceStrengthResult("E4", [], ["chunk_main"], [], ["chunk_main"], ["500kva"], [])
    unchanged = apply_evidence_strength_to_overlay(
        pred,
        already_blocked,
        strong,
        min_strength_for_answered="E3",
        min_strength_for_writeback="E3",
        downgrade_unsupported_answer_to_partial=False,
    )
    assert unchanged.writeback_allowed is False

    global_intro = EvidenceStrengthResult("E2", ["global_intro_only"], ["chunk_global"], [], ["chunk_global"], ["500kva"], [])
    global_blocked = apply_evidence_strength_to_overlay(
        pred,
        overlay,
        global_intro,
        min_strength_for_answered="E3",
        min_strength_for_writeback="E3",
        downgrade_unsupported_answer_to_partial=False,
    )
    assert global_blocked.writeback_allowed is False
    assert global_blocked.review_required is True
    assert "global_intro_only" in global_blocked.reasons


def test_field_binding_exact_numeric_row() -> None:
    result = evaluator().evaluate(
        item=item("已建设机柜数量是多少？"),
        prediction=prediction("12台", ["chunk_main"]),
        top_hits=hits(
            raw_text="现网资源 / 机柜 / 已建设数量：12台。",
            row_header="机柜",
            column_header="已建设数量",
            unit="台",
            table_title="现网资源",
        ),
    )

    assert result.field_binding == "exact"
    assert result.field_binding_score == 1.0


def test_same_value_wrong_field_is_field_mismatch() -> None:
    result = evaluator().evaluate(
        item=item("已建设机柜数量是多少？"),
        prediction=prediction("20台", ["chunk_main"]),
        top_hits=hits(
            raw_text="规划资源 / 机柜 / 规划数量：20台。",
            row_header="机柜",
            column_header="规划数量",
            unit="台",
            table_title="规划资源",
        ),
    )

    assert result.field_binding == "field_mismatch"
    blocked = apply_evidence_strength_to_overlay(
        prediction("20台", ["chunk_main"]),
        overlay_row(writeback_allowed=True, review_required=False, risk_level="low"),
        result,
        min_strength_for_answered="E3",
        min_strength_for_writeback="E3",
        downgrade_unsupported_answer_to_partial=False,
    )
    assert blocked.writeback_allowed is False
    assert blocked.review_required is True
    assert blocked.risk_level == "high"
    assert "field_mismatch" in blocked.reasons


def test_planned_value_for_current_question_is_status_mismatch() -> None:
    result = evaluator().evaluate(
        item=item("当前是否支持双路市电？"),
        prediction=prediction("是", ["chunk_main"]),
        top_hits=hits(raw_text="规划方案支持双路市电接入。", row_header="市电", column_header="规划支持状态"),
    )

    assert result.field_binding == "status_mismatch"


def test_global_scope_for_target_room_is_scope_mismatch() -> None:
    result = evaluator().evaluate(
        item=item("301机房 UPS 容量是多少？"),
        prediction=prediction("500kVA", ["chunk_global"]),
        top_hits=[
            {
                "chunk_id": "chunk_global",
                "namespace": "global",
                "source_type": "intro_doc_paragraph",
                "corpus_layer": "intro_doc",
                "retrieval_layer": "global_intro",
                "raw_text": "园区 UPS 总容量 500kVA。",
                "text_for_embedding": "园区 UPS 总容量 500kVA。",
            }
        ],
    )

    assert result.field_binding == "scope_mismatch"


def test_parent_payload_can_make_binding_parent_exact() -> None:
    result = evaluator().evaluate(
        item=item("301机房 UPS 容量是多少？"),
        prediction=prediction("500kVA", ["chunk_main"]),
        top_hits=hits(
            raw_text="500",
            parent_payload={"system": "UPS系统", "metric": "容量", "unit": "kVA", "room": "301机房"},
        ),
    )

    assert result.field_binding == "parent_exact"
    assert "parent_payload_binds_short_hit" in result.field_binding_reasons


def test_unit_mismatch_blocks_writeback() -> None:
    result = evaluator().evaluate(
        item=item("UPS 容量 kVA 是多少？"),
        prediction=prediction("500kVA", ["chunk_main"]),
        top_hits=hits(raw_text="UPS 功率 500kW。", row_header="UPS", column_header="功率", unit="kW"),
    )

    assert result.field_binding == "unit_mismatch"
    blocked = apply_evidence_strength_to_overlay(
        prediction("500kVA", ["chunk_main"]),
        overlay_row(writeback_allowed=True, review_required=False, risk_level="low"),
        result,
        min_strength_for_answered="E3",
        min_strength_for_writeback="E3",
        downgrade_unsupported_answer_to_partial=False,
    )
    assert blocked.writeback_allowed is False
    assert blocked.review_required is True
    assert blocked.risk_level == "medium"
    assert "unit_mismatch" in blocked.reasons


def test_boolean_condition_mismatch_blocks_writeback() -> None:
    result = evaluator().evaluate(
        item=item("是否已支持双路市电？"),
        prediction=prediction("是", ["chunk_main"]),
        top_hits=hits(raw_text="具备双路市电改造条件。", row_header="市电", column_header="改造条件"),
    )

    assert result.field_binding == "status_mismatch"
    blocked = apply_evidence_strength_to_overlay(
        prediction("是", ["chunk_main"]),
        overlay_row(writeback_allowed=True, review_required=False, risk_level="low"),
        result,
        min_strength_for_answered="E3",
        min_strength_for_writeback="E3",
        downgrade_unsupported_answer_to_partial=False,
    )
    assert blocked.writeback_allowed is False
    assert blocked.review_required is True
    assert "status_mismatch" in blocked.reasons


def test_room_context_does_not_create_scope_mismatch_by_itself() -> None:
    result = evaluator().evaluate(
        item=item("柴发配置是否满足要求？"),
        prediction=prediction("满足", ["chunk_main"]),
        top_hits=hits(raw_text="柴发配置满足要求。", row_header="柴发", column_header="配置情况"),
    )

    assert result.field_binding != "scope_mismatch"


def test_oil_machine_mode_cannot_use_ups_single_mode_evidence() -> None:
    result = evaluator().evaluate(
        item=item("油机运行模式"),
        prediction=prediction("单机", ["chunk_main"]),
        top_hits=hits(
            raw_text="IT-UPS、动力-UPS是否为并机系统：否，全部为单机系统。",
            row_header="IT-UPS、动力-UPS是否为并机系统",
            column_header="现状",
        ),
    )

    assert result.field_binding == "field_mismatch"
    assert "oil_machine_field_cited_non_oil_power_source" in result.field_binding_reasons


def test_oil_parallel_controller_power_cannot_use_oil_route_control_evidence() -> None:
    result = evaluator().evaluate(
        item=item("油机并机控制器电源"),
        prediction=prediction("一路市电，一路U电", ["chunk_main"]),
        top_hits=hits(
            raw_text="油路控制系统电源：一路市电，一路U电。",
            row_header="油路控制系统电源",
            column_header="现状",
        ),
    )

    assert result.field_binding == "field_mismatch"
    assert "oil_parallel_control_confused_with_oil_route_control" in result.field_binding_reasons


def test_chiller_combo_field_requires_pressure_and_redundancy_slots() -> None:
    result = evaluator().evaluate(
        item=item("冰机配置情况", instruction_text="填写高压or低压/冗余情况"),
        prediction=prediction("高压离心式水冷冷水机组", ["chunk_main"]),
        top_hits=hits(
            raw_text="冷水机组配置：高压离心式水冷冷水机组。",
            row_header="冷水机组配置",
            column_header="类型",
        ),
    )

    assert result.field_binding == "slot_mismatch"
    assert "missing_answer_chiller_redundancy_slot" in result.field_binding_reasons


def test_chiller_combo_field_accepts_pressure_and_redundancy_slots() -> None:
    result = evaluator().evaluate(
        item=item("冰机配置情况", instruction_text="填写高压or低压/冗余情况"),
        prediction=prediction("高压/N+1", ["chunk_main"]),
        top_hits=hits(
            raw_text="冷水机组配置：高压离心式水冷冷水机组，系统按N+1冗余配置。",
            row_header="冷水机组配置",
            column_header="类型及冗余",
        ),
    )

    assert result.field_binding in {"exact", "parent_exact"}


def evaluator() -> EvidenceStrengthEvaluator:
    return EvidenceStrengthEvaluator(target_namespace="xixian_4", room_context="西咸4号楼 301机房")


def item(question_text: str, *, instruction_text: str = "") -> dict:
    return {
        "form_item_id": "field_1",
        "row_index": 1,
        "target_cell": "D1",
        "question_text": question_text,
        "instruction_text": instruction_text,
    }


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


def hits(raw_text: str = "UPS容量：500kVA。", **overrides) -> list[dict]:
    hit = {
        "chunk_id": "chunk_main",
        "namespace": "xixian_4",
        "source_type": "main_excel_capability",
        "corpus_layer": "fact",
        "retrieval_layer": "target_main_fact",
        "raw_text": raw_text,
        "text_for_embedding": raw_text,
    }
    hit.update(overrides)
    if "text_for_embedding" not in overrides:
        hit["text_for_embedding"] = raw_text
    return [
        hit
    ]


def overlay_row(*, writeback_allowed: bool, review_required: bool, risk_level: str) -> AgentOverlay:
    return AgentOverlay(
        field_id="field_1",
        row_index=1,
        target_cell="D1",
        critic_flags=[],
        review_required=review_required,
        writeback_allowed=writeback_allowed,
        suggested_status=None,
        suggested_answer_value=None,
        suggested_reference_source_documents=[],
        suggested_reference_chunk_ids=[],
        suggested_reference_snippets=[],
        risk_level=risk_level,
        reasons=[],
    )
