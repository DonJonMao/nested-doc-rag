from __future__ import annotations

from typing import Any

from nested_doc_rag.embedding import RerankClient


def rerank_hits(query_text: str, hits: list[dict[str, Any]], top_n: int, reranker: RerankClient) -> list[dict[str, Any]]:
    if not hits:
        return []
    docs = [hit.get("text_for_embedding") or hit.get("raw_text") or "" for hit in hits]
    reranked = reranker.rerank(query_text, docs, top_n=top_n)
    output: list[dict[str, Any]] = []
    for rank, rerank_item in enumerate(reranked, 1):
        index = int(rerank_item["index"])
        if index < 0 or index >= len(hits):
            continue
        hit = dict(hits[index])
        hit["rerank_rank"] = rank
        hit["rerank_score"] = rerank_item.get("relevance_score")
        output.append(hit)
    return output
