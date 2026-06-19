from __future__ import annotations

from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient, models

from nested_doc_rag.embedding import EmbeddingClient


class QdrantRetriever:
    def __init__(
        self,
        *,
        qdrant_path: Path,
        collection_name: str,
        embedding_endpoint: str,
        embedding_model: str,
        prefer_grpc: bool = False,
        timeout: int = 60,
    ) -> None:
        self.client = QdrantClient(path=str(qdrant_path), prefer_grpc=prefer_grpc, timeout=timeout)
        self.collection_name = collection_name
        self.embedder = EmbeddingClient(endpoint=embedding_endpoint, model=embedding_model)

    def close(self) -> None:
        self.client.close()

    def search(
        self,
        query: str,
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        vector = self.embedder.embed_query(query)
        return self.search_by_vector(
            vector,
            namespaces=namespaces,
            layers=layers,
            source_types=source_types,
            top_k=top_k,
        )

    def search_by_vector(
        self,
        vector: list[float],
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        conditions: list[models.FieldCondition] = [
            models.FieldCondition(key="namespace", match=models.MatchAny(any=namespaces)),
            models.FieldCondition(key="corpus_layer", match=models.MatchAny(any=layers)),
        ]
        if source_types:
            conditions.append(models.FieldCondition(key="source_type", match=models.MatchAny(any=source_types)))
        filters = models.Filter(must=conditions)
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=filters,
            limit=top_k,
            with_payload=True,
        )
        hits: list[dict[str, Any]] = []
        metadata_keys = [
            "source_document",
            "sheet_name",
            "table_id",
            "table_title",
            "section_path",
            "category",
            "category_path",
            "capability_desc",
            "row_header",
            "column_header",
            "unit",
            "row_index",
            "cell_range",
            "scope",
            "status",
            "parent_text",
            "neighbor_text",
            "parent_payload",
        ]
        for rank, point in enumerate(response.points, 1):
            payload = point.payload or {}
            hit = {
                "vector_rank": rank,
                "vector_score": round(float(point.score), 6),
                "chunk_id": payload.get("chunk_id"),
                "namespace": payload.get("namespace"),
                "source_type": payload.get("source_type"),
                "corpus_layer": payload.get("corpus_layer"),
                "anchor": payload.get("anchor"),
                "file_name": payload.get("file_name"),
                "raw_text": payload.get("raw_text"),
                "text_for_embedding": payload.get("text_for_embedding") or payload.get("raw_text"),
                "proof_attachment_ids": payload.get("proof_attachment_ids") or [],
                "source": payload.get("source") or {},
            }
            for key in metadata_keys:
                if key in payload:
                    hit[key] = payload.get(key)
            hits.append(hit)
        return hits
