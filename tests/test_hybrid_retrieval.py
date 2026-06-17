from __future__ import annotations

from typing import Any

from nested_doc_rag.evaluation.step15_engine import run_step15_retrieval
from nested_doc_rag.retrieval.hybrid import hybrid_layered_rerank_hits, rrf_fuse
from nested_doc_rag.retrieval.lexical import BM25Index


def test_rrf_fusion_keeps_dense_only_bm25_only_and_merges_duplicate() -> None:
    fused = rrf_fuse(
        [
            {"chunk_id": "shared", "vector_rank": 1, "vector_score": 0.9, "raw_text": "dense shared"},
            {"chunk_id": "dense_only", "vector_rank": 2, "vector_score": 0.8},
        ],
        [
            {"chunk_id": "bm25_only", "bm25_rank": 1, "bm25_score": 3.0},
            {"chunk_id": "shared", "bm25_rank": 2, "bm25_score": 2.5, "raw_text": "bm25 shared"},
        ],
        rrf_k=60,
    )

    by_id = {hit["chunk_id"]: hit for hit in fused}
    assert {"shared", "dense_only", "bm25_only"} <= set(by_id)
    assert by_id["shared"]["dense_rank"] == 1
    assert by_id["shared"]["bm25_rank"] == 2
    assert by_id["shared"]["rrf_score"] > by_id["dense_only"]["rrf_score"]
    assert "dense_score" in by_id["shared"]
    assert "bm25_score" in by_id["shared"]


def test_hybrid_layered_retrieval_fuses_before_rerank() -> None:
    dense = FakeDenseRetriever()
    lexical = BM25Index.from_records(
        [
            record("lexical_only", "xixian_4", "UPS容量为500kVA。"),
            record("dense_shared", "xixian_4", "UPS供电。"),
        ]
    )
    reranker = FakeReranker()

    hits, dense_hits, trace = hybrid_layered_rerank_hits(
        "500kVA UPS",
        retriever=dense,
        lexical_index=lexical,
        target_namespace="xixian_4",
        global_namespace="global",
        allowed_layers=["fact"],
        reranker=reranker,
        layered_plan=[layer_spec()],
        rrf_k=60,
    )

    assert [hit["chunk_id"] for hit in dense_hits] == ["dense_shared"]
    assert "lexical_only" in [hit["chunk_id"] for hit in hits]
    assert reranker.last_documents is not None
    assert any("500kVA" in document for document in reranker.last_documents)
    assert trace[0]["dense_hit_ids"] == ["dense_shared"]
    assert "lexical_only" in trace[0]["bm25_hit_ids"]
    assert trace[0]["fallback_used"] is False


def test_hybrid_layered_retrieval_falls_back_without_lexical_index() -> None:
    dense = FakeDenseRetriever()
    reranker = FakeReranker()

    hits, _, trace = hybrid_layered_rerank_hits(
        "UPS",
        retriever=dense,
        lexical_index=None,
        target_namespace="xixian_4",
        global_namespace="global",
        allowed_layers=["fact"],
        reranker=reranker,
        layered_plan=[layer_spec()],
        fallback_to_dense=True,
    )

    assert [hit["chunk_id"] for hit in hits] == ["dense_shared"]
    assert trace[0]["fallback_used"] is True
    assert trace[0]["fallback_reason"] == "lexical_index_unavailable"


def test_run_step15_retrieval_dense_path_unchanged() -> None:
    dense = FakeDenseRetriever()
    reranker = FakeReranker()

    result = run_step15_retrieval(
        "UPS",
        retriever=dense,
        reranker=reranker,
        target_namespace="xixian_4",
        global_namespace="global",
        allowed_layers=["fact"],
        retrieval_mode="layered",
        vector_top_k=3,
        rerank_top_n=2,
        layered_plan=[layer_spec()],
        hybrid_enabled=False,
    )

    assert result.retrieval_mode == "layered"
    assert result.trace_records is None
    assert [hit["chunk_id"] for hit in result.reranked_hits] == ["dense_shared"]


class FakeEmbedder:
    def embed_query(self, query: str) -> list[float]:
        del query
        return [0.1, 0.2]


class FakeDenseRetriever:
    collection_name = "fake_collection"

    def __init__(self) -> None:
        self.embedder = FakeEmbedder()

    def search_by_vector(
        self,
        vector: list[float],
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        del vector, source_types, top_k
        if namespaces == ["xixian_4"] and layers == ["fact"]:
            return [record("dense_shared", "xixian_4", "UPS供电。", vector_rank=1)]
        return []

    def search(self, query: str, *, namespaces: list[str], layers: list[str], source_types: list[str] | None = None, top_k: int):
        del query, source_types, top_k
        if namespaces == ["xixian_4", "global"] and layers == ["fact"]:
            return [record("dense_shared", "xixian_4", "UPS供电。", vector_rank=1)]
        return []


class FakeReranker:
    def __init__(self) -> None:
        self.last_documents: list[str] | None = None

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
        del query
        self.last_documents = documents
        return [{"index": index, "relevance_score": 1.0 - index * 0.01} for index in range(min(top_n, len(documents)))]


def layer_spec() -> dict[str, Any]:
    return {
        "layer_name": "target_main_fact",
        "description": "main",
        "namespaces": "target",
        "corpus_layers": ["fact"],
        "source_types": ["main_excel_capability"],
        "vector_top_k": 3,
        "rerank_top_n": 3,
    }


def record(chunk_id: str, namespace: str, raw_text: str, *, vector_rank: int | None = None) -> dict[str, Any]:
    record = {
        "chunk_id": chunk_id,
        "namespace": namespace,
        "corpus_layer": "fact",
        "source_type": "main_excel_capability",
        "raw_text": raw_text,
        "text_for_embedding": raw_text,
    }
    if vector_rank is not None:
        record["vector_rank"] = vector_rank
        record["vector_score"] = 0.9
    return record
