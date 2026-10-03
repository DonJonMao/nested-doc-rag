"""Observe actual local-Qdrant attempts instead of inferring them from plans."""
from __future__ import annotations

import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient, models

from nested_doc_rag.config import load_app_config
from nested_doc_rag.evaluation.step15_engine import (
    build_qdrant_answer_messages,
    normalize_hit_for_prompt,
    run_step15_retrieval,
)
from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever

TARGET = "metrics_room_301"
GLOBAL = "metrics_global"
VECTOR = [1.0, 0.0, 0.0]


class FixedEmbedding:
    def embed_query(self, query: str) -> list[float]:
        return list(VECTOR)


class FixedReranker:
    def rerank(self, query: str, docs: list[str], *, top_n: int) -> list[dict[str, Any]]:
        return [{"index": index, "relevance_score": 1.0} for index in range(min(top_n, len(docs)))]


@pytest.fixture
def actual_retriever(tmp_path: Path) -> Iterator[QdrantRetriever]:
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    client.create_collection("query_metrics", vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    client.upsert("query_metrics", points=[
        models.PointStruct(id=1, vector=VECTOR, payload={
            "chunk_id": "target_ups", "namespace": TARGET, "knowledge_base_id": "kb_target",
            "corpus_layer": "fact", "evidence_kind": "structured_field", "source_type": "uploaded_excel_row",
            "file_name": "匿名能力.xlsx", "relative_path": "匿名能力.xlsx", "sheet_name": "能力", "row_index": 2,
            "cell_range": "B2:C2", "field_name": "UPS容量", "field_value": "500kVA",
            "raw_text": "UPS容量：500kVA。", "raw_source_text": "UPS容量：500kVA。",
        }),
        models.PointStruct(id=2, vector=VECTOR, payload={
            "chunk_id": "global_intro", "namespace": GLOBAL, "knowledge_base_id": "kb_global",
            "corpus_layer": "intro_doc", "evidence_kind": "document_intro", "source_type": "intro_doc_paragraph",
            "file_name": "匿名介绍.docx", "relative_path": "匿名介绍.docx", "paragraph_index": 1,
            "raw_text": "园区介绍。", "raw_source_text": "园区介绍。",
        }),
    ], wait=True)
    # The property must also support the construction used by existing local
    # retrieval tests, without an HTTP embedding service or __init__ call.
    retriever = QdrantRetriever.__new__(QdrantRetriever)
    retriever.client = client
    retriever.collection_name = "query_metrics"
    retriever.embedder = FixedEmbedding()
    try:
        yield retriever
    finally:
        retriever.close()


def full_plan() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    return load_app_config("config/default.yaml", project_root=root, env={}).retrieval.layered_plan


def retrieve(retriever: Any, plan: list[dict[str, Any]], *, layers: list[str] | None = None):
    return run_step15_retrieval(
        "UPS容量", retriever=retriever, reranker=FixedReranker(), target_namespace=TARGET, global_namespace=GLOBAL,
        allowed_layers=layers if layers is not None else ["fact", "evidence", "raw_text", "intro_doc", "meta"],
        retrieval_mode="layered", vector_top_k=10, rerank_top_n=5, layered_plan=plan,
    )


def test_primary_two_and_full_five_measure_actual_local_qdrant_calls(actual_retriever: QdrantRetriever) -> None:
    assert actual_retriever.qdrant_query_calls == 0
    plan = full_plan()
    assert len(plan) == 5
    primary = retrieve(actual_retriever, plan[:2])
    assert primary.metadata["qdrant_query_calls"] == 2
    assert actual_retriever.qdrant_query_calls == 2
    assert {hit["chunk_id"] for hit in primary.reranked_hits} == {"target_ups"}
    complete = retrieve(actual_retriever, plan)
    assert complete.metadata["qdrant_query_calls"] == 5, "Each result records its delta, not the lifetime counter"
    assert actual_retriever.qdrant_query_calls == 7
    assert {hit["chunk_id"] for hit in complete.reranked_hits} == {"target_ups", "global_intro"}


def test_skipped_corpus_layers_are_not_counted_as_qdrant_calls(actual_retriever: QdrantRetriever) -> None:
    result = retrieve(actual_retriever, full_plan(), layers=["fact"])
    assert result.metadata["qdrant_query_calls"] == 4
    assert actual_retriever.qdrant_query_calls == 4
    assert "global_intro" not in {hit["chunk_id"] for hit in result.reranked_hits}


def test_empty_canonical_constraint_does_not_issue_or_count_a_query(actual_retriever: QdrantRetriever) -> None:
    plan = deepcopy(full_plan())
    plan[0]["evidence_kinds"] = []
    result = retrieve(actual_retriever, plan)
    assert result.metadata["qdrant_query_calls"] == 4
    assert actual_retriever.qdrant_query_calls == 4


@pytest.mark.parametrize("plan", [[], full_plan()])
def test_no_executable_layer_means_zero_actual_queries(actual_retriever: QdrantRetriever, plan: list[dict[str, Any]]) -> None:
    result = retrieve(actual_retriever, plan, layers=["meta"])
    assert result.metadata["qdrant_query_calls"] == 0
    assert actual_retriever.qdrant_query_calls == 0
    assert not result.reranked_hits and not result.vector_hits


def test_failed_real_query_attempt_is_counted_immediately(actual_retriever: QdrantRetriever) -> None:
    actual_retriever.collection_name = "nonexistent_collection"
    with pytest.raises(ValueError, match="not found"):
        actual_retriever.search_by_vector(VECTOR, namespaces=[TARGET], layers=["fact"], top_k=10)
    assert actual_retriever.qdrant_query_calls == 1


def test_uninstrumented_fake_reports_unknown_not_configured_plan_length() -> None:
    class UninstrumentedRetriever:
        embedder = FixedEmbedding()

        def __init__(self) -> None:
            self.searches = 0

        def search_by_vector(self, *args, **kwargs) -> list[dict[str, Any]]:
            self.searches += 1
            return []

    fake = UninstrumentedRetriever()
    result = retrieve(fake, full_plan())
    assert fake.searches == 5
    assert result.metadata["qdrant_query_calls"] is None


def test_final_answer_prompt_retains_round_and_trigger_without_changing_layer_rank() -> None:
    hit = {
        "chunk_id": "round_two", "namespace": TARGET, "source_type": "uploaded_excel_row",
        "evidence_kind": "structured_field", "file_name": "能力.xlsx", "sheet_name": "能力", "cell_range": "B2:C2",
        "raw_source_text": "UPS容量：500kVA。", "retrieval_round": 2, "triggered_by": "missing_slot",
        "retrieval_layer": "target_structured_fact", "layer_priority": 1, "final_rank": 3,
    }
    normalized = normalize_hit_for_prompt(hit)
    assert normalized["retrieval_round"] == 2
    assert normalized["triggered_by"] == "missing_slot"
    assert normalized["layer_priority"] == 1 and normalized["rank"] == 3
    item = {
        "form_item_id": "ups", "file_name": "上传调研.xlsx", "sheet_name": "动力", "target_cell": "D2",
        "row_index": 2, "question_text": "UPS容量",
    }
    messages = build_qdrant_answer_messages(item, "UPS容量", [hit])
    user_prompt = messages[1]["content"]
    packed = json.loads(user_prompt.split("retrieved_chunks:\n", 1)[1].split("\n\n请只输出", 1)[0])
    assert packed[0]["retrieval_round"] == 2
    assert packed[0]["triggered_by"] == "missing_slot"
