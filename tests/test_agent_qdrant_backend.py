from __future__ import annotations

from typing import Any

from nested_doc_rag.agent.backends import QdrantEvidenceRetriever, normalize_hit
from nested_doc_rag.agent.policies import build_query_plan
from nested_doc_rag.schemas.eval import FieldGold


def make_field() -> FieldGold:
    return FieldGold.from_dict(
        {
            "field_id": "field_power",
            "row_index": 5,
            "target_cell": "C5",
            "question_text": "是否满足双路供电",
            "expected_value": "满足",
            "field_type": "enum",
            "required": True,
            "must_have_evidence": True,
            "constraints": {"enum_values": ["满足", "不满足"]},
        }
    )


def test_qdrant_hit_normalization() -> None:
    hit = {
        "id": "point_1",
        "score": 0.77,
        "payload": {
            "chunk_id": "chunk_power",
            "namespace": "xixian_4",
            "source_type": "main_excel_capability",
            "corpus_layer": "fact",
            "text_for_embedding": "是否满足双路供电：满足。",
            "raw_text": "是否满足双路供电：满足。",
            "source": {"file_name": "capability.xlsx", "row": 5},
            "evidence_attachment_ids": ["att_power"],
        },
    }

    normalized = normalize_hit(hit)

    assert normalized["chunk_id"] == "chunk_power"
    assert normalized["namespace"] == "xixian_4"
    assert normalized["source_type"] == "main_excel_capability"
    assert normalized["corpus_layer"] == "fact"
    assert normalized["raw_text"] == "是否满足双路供电：满足。"
    assert normalized["source"] == {"file_name": "capability.xlsx", "row": 5}
    assert normalized["evidence_attachment_ids"] == ["att_power"]


def test_qdrant_retriever_normalizes_and_records_metadata() -> None:
    field = make_field()
    plan = build_query_plan(field, target_namespace="xixian_4")
    fake = FakeQdrantRetriever(
        [
            {
                "id": "point_1",
                "vector_score": 0.9,
                "payload": {
                    "chunk_id": "chunk_power",
                    "namespace": "xixian_4",
                    "source_type": "main_excel_capability",
                    "corpus_layer": "fact",
                    "raw_text": "是否满足双路供电：满足。",
                    "source": {"file_name": "capability.xlsx", "row": 5},
                },
            }
        ]
    )
    retriever = QdrantEvidenceRetriever(qdrant_retriever=fake, vector_top_k=3, rerank_top_n=2)

    hits = retriever.retrieve(plan, field)

    assert hits[0]["chunk_id"] == "chunk_power"
    assert hits[0]["namespace"] == "xixian_4"
    assert fake.calls[0]["namespaces"] == ["xixian_4"]
    assert fake.calls[0]["source_types"] == ["main_excel_capability", "embedded_word_table", "intro_doc_paragraph"]
    assert retriever.last_metadata["retrieval_backend"] == "qdrant"
    assert retriever.last_metadata["collection_name"] == "fake_collection"


def test_qdrant_retriever_falls_back_to_global_when_target_is_sparse() -> None:
    field = make_field()
    plan = build_query_plan(field, target_namespace="xixian_4")
    fake = FakeQdrantRetriever([])
    retriever = QdrantEvidenceRetriever(qdrant_retriever=fake, vector_top_k=3, rerank_top_n=2)

    hits = retriever.retrieve(plan, field)

    assert hits == []
    assert ["global"] in [call["namespaces"] for call in fake.calls]
    assert retriever.last_metadata["fallback_used"] is True


class FakeQdrantRetriever:
    collection_name = "fake_collection"

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    def search(
        self,
        query: str,
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "query": query,
                "namespaces": namespaces,
                "layers": layers,
                "source_types": source_types,
                "top_k": top_k,
            }
        )
        return self.hits if namespaces == ["xixian_4"] else []
