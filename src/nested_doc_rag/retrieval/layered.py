from __future__ import annotations

from typing import Any

from nested_doc_rag.embedding import RerankClient
from nested_doc_rag.evidence_record import normalize_evidence_record
from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever
from nested_doc_rag.retrieval.rerank import rerank_hits


def constrain_layered_plan(
    layered_plan: list[dict[str, Any]],
    *,
    layer_names: list[str] | None = None,
    source_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Narrow a configured plan without allowing actions to invent scope or sources."""
    if any(spec.get("namespaces") not in ("target", "global") for spec in layered_plan):
        raise ValueError("layer namespace must be target or global")
    configured_layers = {str(spec["layer_name"]) for spec in layered_plan}
    configured_sources = {str(source) for spec in layered_plan for source in spec.get("source_types", [])}
    wildcard_sources = any(not spec.get("source_types") for spec in layered_plan)
    if layer_names is not None and set(layer_names) - configured_layers:
        raise ValueError("retrieval action requested an unconfigured layer")
    if source_types is not None and set(source_types) - configured_sources and not wildcard_sources:
        raise ValueError("retrieval action requested an unconfigured source type")
    output: list[dict[str, Any]] = []
    for spec in layered_plan:
        if layer_names is not None and spec["layer_name"] not in layer_names:
            continue
        copied = dict(spec)
        if source_types is not None:
            if not source_types:
                continue
            if "evidence_kinds" in spec:
                # The legacy candidate list does not restrict canonical points.
                copied["required_source_types"] = list(source_types)
                copied["source_types"] = [source for source in spec.get("source_types", []) if source in source_types]
                output.append(copied)
                continue
            # An empty configured list means all source types in Qdrant. An
            # action's empty list instead explicitly requests no retrieval.
            copied["source_types"] = (
                [source for source in spec.get("source_types", []) if source in source_types]
                if spec.get("source_types") else list(source_types)
            )
            if not copied["source_types"]:
                continue
            copied["required_source_types"] = list(copied["source_types"])
        output.append(copied)
    return output


def filter_hits_by_plan(
    hits: list[dict[str, Any]],
    *,
    layered_plan: list[dict[str, Any]],
    target_namespace: str,
    global_namespace: str,
    allowed_layers: list[str],
) -> list[dict[str, Any]]:
    """Apply the same scope contract to injected retrieval backends."""
    if any(spec.get("namespaces") not in ("target", "global") for spec in layered_plan):
        raise ValueError("layer namespace must be target or global")
    output: list[dict[str, Any]] = []
    for raw_hit in hits:
        if raw_hit.get("retrieval_object") == "field_schema":
            continue
        hit = normalize_evidence_record(raw_hit)
        for spec in layered_plan:
            namespace = target_namespace if spec["namespaces"] == "target" else global_namespace
            if (
                hit.get("namespace") == namespace
                and hit.get("corpus_layer") in spec["corpus_layers"]
                and hit.get("corpus_layer") in allowed_layers
                and (
                    "evidence_kinds" not in spec
                    or bool(raw_hit.get("evidence_kind"))
                    or raw_hit.get("source_type") in spec.get("source_types", [])
                )
                and (
                    hit.get("evidence_kind") in spec["evidence_kinds"]
                    if "evidence_kinds" in spec
                    else (not spec.get("source_types") or hit.get("source_type") in spec["source_types"])
                )
                and ("required_source_types" not in spec or hit.get("source_type") in spec["required_source_types"])
                and (not hit.get("retrieval_layer") or hit["retrieval_layer"] == spec["layer_name"])
            ):
                output.append(dict(hit))
                break
    return output


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
    schema_first_enabled: bool = False,
    schema_queries: list[str] | None = None,
    schema_selections: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed_layer_set = set(allowed_layers)
    if not layered_plan:
        return [], []
    query_vector = retriever.embedder.embed_query(query_text)
    final_hits: list[dict[str, Any]] = []
    vector_hits: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    selected_by_namespace: dict[str, list[str] | None] = {}

    for layer_priority, spec in enumerate(layered_plan, 1):
        corpus_layers = [layer for layer in spec["corpus_layers"] if layer in allowed_layer_set]
        if not corpus_layers:
            continue
        if spec["namespaces"] not in {"target", "global"}:
            raise ValueError("layer namespace must be target or global")
        namespaces = [target_namespace] if spec["namespaces"] == "target" else [global_namespace]
        canonical_options: dict[str, Any] = {}
        if "evidence_kinds" in spec:
            canonical_options["evidence_kinds"] = list(spec["evidence_kinds"])
        if "required_source_types" in spec:
            canonical_options["required_source_types"] = list(spec["required_source_types"])
        table_layer = bool(set(spec.get("evidence_kinds", [])) & {"structured_field", "table_row"}) or bool(
            set(spec.get("source_types", [])) & {"main_excel_capability", "uploaded_excel_row"}
        )
        if schema_first_enabled and table_layer:
            namespace = namespaces[0]
            if namespace not in selected_by_namespace:
                selection = select_field_families(
                    schema_queries or [query_text], retriever=retriever, reranker=reranker,
                    namespace=namespace, top_k=int(spec["vector_top_k"]),
                )
                selected_by_namespace[namespace] = selection["field_family_ids"]
                if schema_selections is not None:
                    schema_selections.append(selection)
            # None is an explicit no-schema compatibility fallback. Empty or
            # populated selections constrain Excel values without broadening.
            if selected_by_namespace[namespace] is not None:
                canonical_options["field_family_ids"] = selected_by_namespace[namespace]
        layer_vector_hits = retriever.search_by_vector(
            query_vector,
            namespaces=namespaces,
            layers=corpus_layers,
            source_types=spec.get("source_types"),
            top_k=int(spec["vector_top_k"]),
            **canonical_options,
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


def select_field_families(
    queries: list[str], *, retriever: QdrantRetriever, reranker: RerankClient,
    namespace: str, top_k: int,
) -> dict[str, Any]:
    """Choose one schema per required fact using the existing reranker."""
    families: list[str] = []
    details: list[dict[str, Any]] = []
    had_candidates = False
    for query in dict.fromkeys(queries):
        candidates = retriever.search_field_schemas(query, namespaces=[namespace], top_k=top_k)
        had_candidates = had_candidates or bool(candidates)
        selected = rerank_hits(query, candidates, 1, reranker)
        valid = [hit for hit in selected if hit.get("retrieval_object") == "field_schema"
                 and hit.get("namespace") == namespace and isinstance(hit.get("field_family_id"), str) and hit["field_family_id"]]
        families.extend(hit["field_family_id"] for hit in valid)
        details.append({
            "query": query, "candidate_schema_ids": [hit.get("schema_id") for hit in candidates],
            "selected_schema_ids": [hit.get("schema_id") for hit in valid],
            "field_family_ids": [hit["field_family_id"] for hit in valid],
            "diagnostic": "no_schema" if not candidates else ("invalid_schema_selection" if not valid else None),
        })
    return {
        "namespace": namespace, "queries": details,
        "field_family_ids": list(dict.fromkeys(families)) if had_candidates else None,
        "fallback": None if had_candidates else "no_schema",
    }
