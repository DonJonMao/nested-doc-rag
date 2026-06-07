from __future__ import annotations

import json
from pathlib import Path

from nested_doc_rag.agent.runner import FieldFillingAgent
from nested_doc_rag.io import read_jsonl, write_jsonl
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction


def make_fields() -> list[FieldGold]:
    return [
        FieldGold.from_dict(
            {
                "field_id": f"field_{index}",
                "row_index": index,
                "target_cell": f"C{index}",
                "question_text": "机房名称",
                "expected_value": "unused",
                "field_type": "text",
                "required": True,
                "must_have_evidence": True,
            }
        )
        for index in range(1, 4)
    ]


def test_checkpoint_writes_each_field(tmp_path: Path) -> None:
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        out_dir=tmp_path,
        retriever=PerFieldRetriever(),
        checkpoint_every=1,
    )

    predictions = agent.run(make_fields())

    assert len(predictions) == 3
    assert len(read_jsonl(tmp_path / "predictions.checkpoint.jsonl")) == 3
    assert (tmp_path / "trace.checkpoint.jsonl").exists()
    assert (tmp_path / "review_items.checkpoint.jsonl").exists()
    assert json.loads((tmp_path / "run_state.json").read_text(encoding="utf-8"))["fields_completed"] == 3


def test_resume_skips_completed_fields(tmp_path: Path) -> None:
    fields = make_fields()
    completed = FieldPrediction(
        field_id="field_1",
        row_index=1,
        target_cell="C1",
        answer_value="already done",
        answer_status="answered",
        confidence=0.9,
        source_chunk_ids=["chunk_field_1"],
        method_name="checkpoint",
    )
    write_jsonl(tmp_path / "predictions.checkpoint.jsonl", [completed.to_dict()])
    retriever = PerFieldRetriever()
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        out_dir=tmp_path,
        retriever=retriever,
        resume=True,
        checkpoint_every=1,
    )

    predictions = agent.run(fields)
    summary = (tmp_path / "run_summary.md").read_text(encoding="utf-8")

    assert [prediction.field_id for prediction in predictions] == ["field_1", "field_2", "field_3"]
    assert retriever.called_field_ids == ["field_2", "field_3"]
    assert "- skipped_completed_count: 1" in summary


class PerFieldRetriever:
    last_metadata = {"qdrant_hit_count": 0}

    def __init__(self) -> None:
        self.called_field_ids: list[str] = []

    def retrieve(self, query_plan, field):  # noqa: ANN001
        self.called_field_ids.append(field.field_id)
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
