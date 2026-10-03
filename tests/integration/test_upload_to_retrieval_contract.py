from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from qdrant_client import QdrantClient

from nested_doc_rag.config import load_app_config
from nested_doc_rag.evaluation.step15_engine import run_step15_retrieval
from nested_doc_rag.ingestion import build_ingestion_records, upsert_records
from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FixedEmbedding:
    """Exercise real Qdrant filters without an embedding service."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, query: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FixedReranker:
    def rerank(self, query: str, docs: list[str], *, top_n: int) -> list[dict[str, Any]]:
        return [{"index": index, "relevance_score": 1.0} for index in range(min(top_n, len(docs)))]


def test_uploaded_excel_record_is_retrievable_by_production_plan(tmp_path: Path) -> None:
    """Upload -> native extraction -> actual Qdrant -> Docker production plan."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "供电能力"
    worksheet.append(["字段", "当前实际值"])
    worksheet.append(["UPS单台容量", "500 kVA"])
    workbook.save(upload_dir / "新上传_供电知识.xlsx")
    workbook.close()

    namespace = "phase0_room_301"
    records, skipped = build_ingestion_records(upload_dir, namespace=namespace, knowledge_base_id="phase0_kb")
    assert not skipped
    fact = next(record for record in records if "500 kVA" in record["raw_text"])
    config = load_app_config(project_root=PROJECT_ROOT, default_config=PROJECT_ROOT / "config/docker.yaml", env={})
    embedder = FixedEmbedding()
    client = QdrantClient(":memory:")
    try:
        count, dimension = upsert_records(
            qdrant=client,
            collection_name="phase0_uploads",
            records=records,
            embedder=embedder,
            batch_size=8,
            namespace=namespace,
        )
        assert count == len(records)
        assert dimension == 3
        retriever = QdrantRetriever.__new__(QdrantRetriever)
        retriever.client = client
        retriever.collection_name = "phase0_uploads"
        retriever.embedder = embedder

        # This control proves the record exists and is queryable; an empty
        # production result cannot be blamed on a stub or a failed upsert.
        direct_hits = retriever.search_by_vector(
            embedder.embed_query("UPS单台容量"), namespaces=[namespace], layers=["fact"], top_k=10
        )
        assert fact["chunk_id"] in {hit["chunk_id"] for hit in direct_hits}

        result = run_step15_retrieval(
            "301机房 UPS单台容量",
            retriever=retriever,
            reranker=FixedReranker(),
            target_namespace=namespace,
            global_namespace="phase0_global",
            allowed_layers=config.retrieval.query_layers,
            retrieval_mode=config.retrieval.plan,
            vector_top_k=config.retrieval.vector_top_k,
            rerank_top_n=config.retrieval.rerank_top_n,
            layered_plan=config.retrieval.layered_plan,
        )
        assert fact["chunk_id"] in {hit["chunk_id"] for hit in result.reranked_hits}, (
            "Uploaded fact is present in actual Qdrant but absent from the Docker production plan: "
            f"source_type={fact.get('source_type')}, vector_hits={len(result.vector_hits)}, "
            f"reranked_hits={len(result.reranked_hits)}"
        )
    finally:
        client.close()
