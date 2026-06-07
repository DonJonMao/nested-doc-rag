from __future__ import annotations

from pathlib import Path
from typing import Any

from nested_doc_rag.agent.runner import FieldFillingAgent
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction


def make_field(field_id: str, question_text: str, field_type: str = "text") -> FieldGold:
    return FieldGold.from_dict(
        {
            "field_id": field_id,
            "row_index": int(field_id.rsplit("_", 1)[-1]) if field_id.rsplit("_", 1)[-1].isdigit() else 1,
            "target_cell": "C1",
            "question_text": question_text,
            "expected_value": "unused",
            "field_type": field_type,
            "required": True,
            "must_have_evidence": True,
        }
    )


def test_llm_not_called_for_reference_only(tmp_path: Path) -> None:
    field = make_field("field_1", "UPS容量", "number")
    generator = RecordingGenerator()
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        out_dir=tmp_path,
        retriever=StaticRetriever(
            [
                {
                    "chunk_id": "global_ups",
                    "namespace": "global",
                    "source_type": "intro_doc_paragraph",
                    "corpus_layer": "intro_doc",
                    "retrieval_layer": "global_intro",
                    "raw_text": "园区 UPS 容量说明，目标机房需另查。",
                }
            ]
        ),
        answer_generator=generator,
        generation_backend="llm",
    )

    predictions = agent.run([field])

    assert generator.called is False
    assert predictions[0].answer_status == "partial_clue"
    assert predictions[0].reference_chunk_ids == ["global_ups"]


def test_llm_called_only_for_direct(tmp_path: Path) -> None:
    field = make_field("field_1", "是否满足双路供电", "enum")
    generator = RecordingGenerator()
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        out_dir=tmp_path,
        retriever=StaticRetriever(
            [
                {
                    "chunk_id": "target_power",
                    "namespace": "xixian_4",
                    "source_type": "main_excel_capability",
                    "corpus_layer": "fact",
                    "retrieval_layer": "target_main_fact",
                    "field_id": "field_1",
                    "answer_value": "满足",
                    "answer_status": "answered",
                    "source_chunk_ids": ["target_power"],
                    "raw_text": "是否满足双路供电：满足。",
                }
            ]
        ),
        answer_generator=generator,
        generation_backend="llm",
    )

    predictions = agent.run([field])

    assert generator.called is True
    assert predictions[0].answer_status == "answered"
    assert predictions[0].source_chunk_ids == ["target_power"]


def test_field_failure_does_not_abort_run(tmp_path: Path) -> None:
    fields = [
        make_field("field_1", "机房名称"),
        make_field("field_2", "机房名称"),
        make_field("field_3", "机房名称"),
    ]
    generator = FailingGenerator(fail_field_id="field_2")
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        out_dir=tmp_path,
        retriever=PerFieldRetriever(),
        answer_generator=generator,
        generation_backend="llm",
    )

    predictions = agent.run(fields)

    assert [prediction.field_id for prediction in predictions] == ["field_1", "field_2", "field_3"]
    assert predictions[1].answer_status == "conflict_unresolved"
    assert predictions[2].answer_status == "answered"


class StaticRetriever:
    last_metadata = {"qdrant_hit_count": 0}

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits

    def retrieve(self, query_plan, field):  # noqa: ANN001
        return self.hits


class PerFieldRetriever:
    last_metadata = {"qdrant_hit_count": 0}

    def retrieve(self, query_plan, field):  # noqa: ANN001
        return [
            {
                "chunk_id": f"chunk_{field.field_id}",
                "namespace": query_plan.target_namespace,
                "source_type": "main_excel_capability",
                "corpus_layer": "fact",
                "retrieval_layer": "target_main_fact",
                "field_id": field.field_id,
                "answer_value": f"answer {field.field_id}",
                "answer_status": "answered",
                "source_chunk_ids": [f"chunk_{field.field_id}"],
                "raw_text": f"机房名称：answer {field.field_id}",
            }
        ]


class RecordingGenerator:
    chat_model = "fake"

    def __init__(self) -> None:
        self.called = False

    def generate(self, field, evidence_bundle, query_plan, *, trace_context=None):  # noqa: ANN001
        self.called = True
        return FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value=evidence_bundle.selected_chunks[0].get("answer_value", "answered"),
            answer_status="answered",
            confidence=0.9,
            source_chunk_ids=[evidence_bundle.selected_chunks[0]["chunk_id"]],
            validation={"generation_backend": "fake"},
            method_name="fake",
        )


class FailingGenerator(RecordingGenerator):
    def __init__(self, fail_field_id: str) -> None:
        super().__init__()
        self.fail_field_id = fail_field_id

    def generate(self, field, evidence_bundle, query_plan, *, trace_context=None):  # noqa: ANN001
        if field.field_id == self.fail_field_id:
            raise RuntimeError("boom")
        return super().generate(field, evidence_bundle, query_plan, trace_context=trace_context)
