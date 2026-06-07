from __future__ import annotations

import json
from typing import Any

from nested_doc_rag.agent.backends import LLMAnswerGenerator
from nested_doc_rag.agent.policies import build_query_plan
from nested_doc_rag.agent.state import EvidenceBundle
from nested_doc_rag.schemas.eval import FieldGold


def make_field() -> FieldGold:
    return FieldGold.from_dict(
        {
            "field_id": "field_room_name",
            "row_index": 4,
            "target_cell": "C4",
            "question_text": "机房名称是什么",
            "expected_value": "SHOULD_NOT_LEAK",
            "field_type": "text",
            "required": True,
            "must_have_evidence": True,
        }
    )


def make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        field_id="field_room_name",
        selected_chunks=[
            {
                "chunk_id": "chunk_room",
                "namespace": "xixian_4",
                "source_type": "main_excel_capability",
                "corpus_layer": "fact",
                "raw_text": "机房名称：西咸4号楼301机房。",
                "source": {"file_name": "demo.xlsx", "sheet": "Sheet1", "row": 4},
                "evidence_attachment_ids": ["att_1"],
            }
        ],
        reference_chunks=[],
        ignored_chunks=[],
        decision="use_direct_evidence",
        reason="direct target evidence",
    )


def test_llm_called_for_direct_evidence() -> None:
    field = make_field()
    http = FakeHttp(
        {
            "answer_value": "西咸4号楼301机房",
            "answer_status": "answered",
            "confidence": 0.99,
            "source_chunk_ids": ["chunk_room"],
            "evidence_attachment_ids": ["att_1", "att_missing"],
            "reason": "selected evidence states the room name",
        }
    )
    generator = LLMAnswerGenerator(chat_endpoint="http://chat", chat_model="demo-model", api_key="secret", http_client=http)

    prediction = generator.generate(field, make_bundle(), build_query_plan(field, target_namespace="xixian_4"))

    assert prediction.answer_status == "answered"
    assert prediction.answer_value == "西咸4号楼301机房"
    assert prediction.source_chunk_ids == ["chunk_room"]
    assert prediction.evidence_attachment_ids == ["att_1"]
    assert prediction.confidence == 0.95
    assert prediction.validation["chat_model"] == "demo-model"
    assert http.calls[0]["headers"] == {"Authorization": "Bearer secret"}
    assert "SHOULD_NOT_LEAK" not in json.dumps(http.calls[0]["payload"], ensure_ascii=False)


def test_llm_invalid_source_chunk_is_removed_or_flagged() -> None:
    field = make_field()
    http = FakeHttp(
        {
            "answer_value": "西咸4号楼301机房",
            "answer_status": "answered",
            "confidence": 0.88,
            "source_chunk_ids": ["missing_chunk"],
            "evidence_attachment_ids": [],
            "reason": "bad source",
        }
    )
    generator = LLMAnswerGenerator(chat_endpoint="http://chat", chat_model="demo-model", http_client=http)

    prediction = generator.generate(field, make_bundle(), build_query_plan(field, target_namespace="xixian_4"))

    assert prediction.answer_status == "partial_clue"
    assert prediction.source_chunk_ids == []
    assert prediction.validation["invalid_source_reference"] == ["missing_chunk"]
    assert prediction.validation["missing_evidence"] is True


def test_llm_invalid_json_becomes_reviewable_prediction() -> None:
    field = make_field()
    generator = LLMAnswerGenerator(chat_endpoint="http://chat", chat_model="demo-model", http_client=FakeHttp("not json"))

    prediction = generator.generate(field, make_bundle(), build_query_plan(field, target_namespace="xixian_4"))

    assert prediction.answer_status == "conflict_unresolved"
    assert prediction.validation["generation_backend"] == "llm"
    assert "generation_error" in prediction.validation


class FakeHttp:
    def __init__(self, content: dict[str, Any] | str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def post_json(self, url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        content = self.content if isinstance(self.content, str) else json.dumps(self.content, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}
