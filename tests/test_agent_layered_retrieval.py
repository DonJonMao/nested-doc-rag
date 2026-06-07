from __future__ import annotations

from typing import Any

from nested_doc_rag.agent.backends import LayeredQdrantEvidenceRetriever
from nested_doc_rag.agent.policies import build_query_plan
from nested_doc_rag.schemas.eval import FieldGold


def make_field() -> FieldGold:
    return FieldGold.from_dict(
        {
            "field_id": "field_power",
            "row_index": 1,
            "target_cell": "C1",
            "question_text": "是否满足双路供电",
            "expected_value": "unused",
            "field_type": "enum",
            "required": True,
            "must_have_evidence": True,
        }
    )


def test_layered_qdrant_retriever_annotates_layers() -> None:
    field = make_field()
    plan = build_query_plan(field, target_namespace="xixian_4")
    fake = FakeQdrantRetriever()
    retriever = LayeredQdrantEvidenceRetriever(
        qdrant_retriever=fake,
        layered_plan=[
            {
                "layer_name": "target_main_fact",
                "description": "main",
                "namespaces": "target",
                "corpus_layers": ["fact"],
                "source_types": ["main_excel_capability"],
                "vector_top_k": 3,
                "rerank_top_n": 2,
            },
            {
                "layer_name": "global_intro",
                "description": "intro",
                "namespaces": "global",
                "corpus_layers": ["intro_doc"],
                "source_types": ["intro_doc_paragraph"],
                "vector_top_k": 3,
                "rerank_top_n": 2,
            },
        ],
        global_namespace="global",
        vector_top_k=3,
        rerank_top_n=2,
    )

    hits = retriever.retrieve(plan, field)

    assert [hit["retrieval_layer"] for hit in hits] == ["target_main_fact", "global_intro"]
    assert hits[0]["layer_rank"] == 1
    assert hits[0]["query_used"] == plan.primary_query
    assert retriever.last_metadata["retrieval_plan"] == "layered"
    assert retriever.last_metadata["layer_counts"] == {"target_main_fact": 1, "global_intro": 1}
    assert fake.calls[0]["namespaces"] == ["xixian_4"]
    assert fake.calls[1]["namespaces"] == ["global"]


class FakeQdrantRetriever:
    collection_name = "fake_collection"

    def __init__(self) -> None:
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
        if namespaces == ["xixian_4"]:
            return [
                {
                    "id": "point_power",
                    "vector_score": 0.9,
                    "payload": {
                        "chunk_id": "target_power",
                        "namespace": "xixian_4",
                        "source_type": "main_excel_capability",
                        "corpus_layer": "fact",
                        "raw_text": "是否满足双路供电：满足。",
                    },
                }
            ]
        return [
            {
                "id": "point_global",
                "vector_score": 0.6,
                "payload": {
                    "chunk_id": "global_power",
                    "namespace": "global",
                    "source_type": "intro_doc_paragraph",
                    "corpus_layer": "intro_doc",
                    "raw_text": "园区双路供电背景说明。",
                },
            }
        ]
