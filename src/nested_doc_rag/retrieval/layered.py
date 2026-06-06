from __future__ import annotations

from typing import Any

from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever
from nested_doc_rag.retrieval.rerank import rerank_hits


def annotate_layer_hits(
    hits: list[dict[str, Any]],
    *,
    layer_name: str,
    layer_priority: int,
    layer_description: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for hit in hits:
        copied = dict(hit)
        copied["retrieval_layer"] = layer_name
        copied["layer_priority"] = layer_priority
        copied["layer_description"] = layer_description
        output.append(copied)
    return output


def layered_rerank_hits(
    query_text: str,
    *,
    retriever: QdrantRetriever,
    target_namespace: str,
    global_namespace: str,
    allowed_layers: list[str],
    reranker: RerankClient,
    layered_plan: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed_layer_set = set(allowed_layers)
    query_vector = retriever.embedder.embed_query(query_text)
    final_hits: list[dict[str, Any]] = []
    vector_hits: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()

    for layer_priority, spec in enumerate(layered_plan, 1):
        corpus_layers = [layer for layer in spec["corpus_layers"] if layer in allowed_layer_set]
        if not corpus_layers:
            continue
        namespaces = [target_namespace] if spec["namespaces"] == "target" else [global_namespace]
        layer_vector_hits = retriever.search_by_vector(
            query_vector,
            namespaces=namespaces,
            layers=corpus_layers,
            source_types=spec["source_types"],
            top_k=int(spec["vector_top_k"]),
        )
        layer_vector_hits = annotate_layer_hits(
            layer_vector_hits,
            layer_name=str(spec["layer_name"]),
            layer_priority=layer_priority,
            layer_description=str(spec["description"]),
        )
        vector_hits.extend(layer_vector_hits)
        layer_reranked = rerank_hits(query_text, layer_vector_hits, int(spec["rerank_top_n"]), reranker)
        for hit in layer_reranked:
            chunk_id = str(hit.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_chunk_ids:
                continue
            if chunk_id:
                seen_chunk_ids.add(chunk_id)
            final_hits.append(hit)

    for final_rank, hit in enumerate(final_hits, 1):
        hit["final_rank"] = final_rank
    return final_hits, vector_hits
