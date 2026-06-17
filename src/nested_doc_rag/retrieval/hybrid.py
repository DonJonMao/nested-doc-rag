from __future__ import annotations

from typing import Any

from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.retrieval.lexical import BM25Index
from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever
from nested_doc_rag.retrieval.rerank import rerank_hits


def rrf_fuse(
    dense_hits: list[dict[str, Any]],
    bm25_hits: list[dict[str, Any]],
    *,
    rrf_k: int = 60,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for rank, hit in enumerate(dense_hits, 1):
        chunk_id = chunk_key(hit, rank, "dense")
        if chunk_id not in records:
            records[chunk_id] = dict(hit)
            order.append(chunk_id)
        record = records[chunk_id]
        dense_rank = int(hit.get("dense_rank") or hit.get("vector_rank") or rank)
        record.update({key: value for key, value in hit.items() if value is not None})
        record["dense_rank"] = dense_rank
        record["dense_score"] = hit.get("dense_score", hit.get("vector_score", hit.get("score")))
    for rank, hit in enumerate(bm25_hits, 1):
        chunk_id = chunk_key(hit, rank, "bm25")
        if chunk_id not in records:
            records[chunk_id] = dict(hit)
            order.append(chunk_id)
        else:
            records[chunk_id] = merge_metadata(records[chunk_id], hit)
        record = records[chunk_id]
        bm25_rank = int(hit.get("bm25_rank") or rank)
        record["bm25_rank"] = bm25_rank
        record["bm25_score"] = hit.get("bm25_score", hit.get("score"))
    fused: list[dict[str, Any]] = []
    for chunk_id in order:
        record = records[chunk_id]
        score = 0.0
        if record.get("dense_rank") is not None:
            score += 1.0 / (rrf_k + int(record["dense_rank"]))
        if record.get("bm25_rank") is not None:
            score += 1.0 / (rrf_k + int(record["bm25_rank"]))
        record["chunk_id"] = str(record.get("chunk_id") or chunk_id)
        record["rrf_score"] = round(score, 8)
        record["score"] = record["rrf_score"]
        record["retrieval_fusion"] = "rrf"
        fused.append(record)
    fused.sort(key=lambda item: item.get("rrf_score") or 0.0, reverse=True)
    for rank, hit in enumerate(fused, 1):
        hit["fusion_rank"] = rank
    return fused[:top_k] if top_k else fused


def hybrid_layered_rerank_hits(
    query_text: str,
    *,
    retriever: QdrantRetriever,
    lexical_index: BM25Index | None,
    target_namespace: str,
    global_namespace: str,
    allowed_layers: list[str],
    reranker: RerankClient,
    layered_plan: list[dict[str, Any]],
    rrf_k: int = 60,
    dense_top_k: int | None = None,
    bm25_top_k: int | None = None,
    fusion_top_k: int | None = None,
    fallback_to_dense: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    allowed_layer_set = set(allowed_layers)
    query_vector = retriever.embedder.embed_query(query_text)
    final_hits: list[dict[str, Any]] = []
    dense_hits: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    trace_records: list[dict[str, Any]] = []

    for layer_priority, spec in enumerate(layered_plan, 1):
        corpus_layers = [str(layer) for layer in spec.get("corpus_layers") or [] if str(layer) in allowed_layer_set]
        if not corpus_layers:
            continue
        layer_name = str(spec.get("layer_name") or f"layer_{layer_priority}")
        namespaces = [target_namespace] if spec.get("namespaces") == "target" else [global_namespace]
        source_types = [str(item) for item in spec.get("source_types") or []] or None
        per_layer_dense_top_k = int(dense_top_k or spec.get("vector_top_k") or 10)
        per_layer_bm25_top_k = int(bm25_top_k or spec.get("vector_top_k") or per_layer_dense_top_k)
        per_layer_fusion_top_k = int(fusion_top_k or spec.get("vector_top_k") or max(per_layer_dense_top_k, per_layer_bm25_top_k))

        layer_dense_hits = retriever.search_by_vector(
            query_vector,
            namespaces=namespaces,
            layers=corpus_layers,
            source_types=source_types,
            top_k=per_layer_dense_top_k,
        )
        dense_hits.extend(layer_dense_hits)
        layer_bm25_hits: list[dict[str, Any]] = []
        fallback_reason = ""
        if lexical_index is None:
            fallback_reason = "lexical_index_unavailable"
        else:
            try:
                layer_bm25_hits = lexical_index.search(
                    query_text,
                    namespaces=namespaces,
                    layers=corpus_layers,
                    source_types=source_types,
                    top_k=per_layer_bm25_top_k,
                )
            except Exception as exc:  # noqa: BLE001 - lexical retrieval must not break dense path
                if not fallback_to_dense:
                    raise
                fallback_reason = f"lexical_search_failed: {exc}"
                layer_bm25_hits = []

        fused_hits = rrf_fuse(
            layer_dense_hits,
            layer_bm25_hits,
            rrf_k=rrf_k,
            top_k=per_layer_fusion_top_k,
        )
        annotated = [
            annotate_hybrid_layer_hit(
                hit,
                layer_name=layer_name,
                layer_priority=layer_priority,
                layer_description=str(spec.get("description") or ""),
                layer_rank=rank,
                query_used=query_text,
            )
            for rank, hit in enumerate(fused_hits, 1)
        ]
        if annotated:
            annotated = rerank_hits(query_text, annotated, int(spec.get("rerank_top_n") or len(annotated)), reranker)
            for rank, hit in enumerate(annotated, 1):
                hit["layer_rank"] = rank
                hit["layer_score"] = hit.get("rerank_score") or hit.get("rrf_score") or hit.get("vector_score") or hit.get("bm25_score")
        for hit in annotated:
            chunk_id = str(hit.get("chunk_id") or "")
            if chunk_id and chunk_id in seen_chunk_ids:
                continue
            if chunk_id:
                seen_chunk_ids.add(chunk_id)
            final_hits.append(hit)
        trace_records.append(
            {
                "query_text": query_text,
                "retrieval_mode": "hybrid",
                "layer_name": layer_name,
                "namespaces": namespaces,
                "corpus_layers": corpus_layers,
                "source_types": source_types or [],
                "dense_hit_ids": hit_ids(layer_dense_hits),
                "bm25_hit_ids": hit_ids(layer_bm25_hits),
                "fused_hit_ids": hit_ids(fused_hits),
                "rrf_k": rrf_k,
                "rrf_scores": [
                    {
                        "chunk_id": hit.get("chunk_id"),
                        "dense_rank": hit.get("dense_rank"),
                        "bm25_rank": hit.get("bm25_rank"),
                        "rrf_score": hit.get("rrf_score"),
                    }
                    for hit in fused_hits
                ],
                "fallback_used": bool(fallback_reason),
                "fallback_reason": fallback_reason,
            }
        )

    for final_rank, hit in enumerate(final_hits, 1):
        hit["final_rank"] = final_rank
    return final_hits, dense_hits, trace_records


def annotate_hybrid_layer_hit(
    hit: dict[str, Any],
    *,
    layer_name: str,
    layer_priority: int,
    layer_description: str,
    layer_rank: int,
    query_used: str,
) -> dict[str, Any]:
    layer_score = hit.get("rrf_score") or hit.get("vector_score") or hit.get("bm25_score") or hit.get("score") or 0.0
    return {
        **hit,
        "retrieval_layer": layer_name,
        "layer_priority": layer_priority,
        "layer_description": layer_description,
        "layer_rank": layer_rank,
        "layer_score": layer_score,
        "query_used": query_used,
    }


def merge_metadata(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if is_empty_metadata_value(merged.get(key)) and not is_empty_metadata_value(value):
            merged[key] = value
    return merged


def is_empty_metadata_value(value: Any) -> bool:
    return value is None or value == "" or value == []


def chunk_key(hit: dict[str, Any], rank: int, prefix: str) -> str:
    return str(hit.get("chunk_id") or hit.get("id") or hit.get("point_id") or f"{prefix}_{rank}")


def hit_ids(hits: list[dict[str, Any]]) -> list[str]:
    return [str(hit.get("chunk_id") or hit.get("id") or hit.get("point_id")) for hit in hits if hit.get("chunk_id") or hit.get("id") or hit.get("point_id")]
