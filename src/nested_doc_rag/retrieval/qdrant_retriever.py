from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from qdrant_client import models

from nested_doc_rag.embedding import EmbeddingClient
from nested_doc_rag.evidence_record import normalize_evidence_record
from nested_doc_rag.retrieval.qdrant_client import build_qdrant_client
from nested_doc_rag.retrieval.version_scope import (
    LEGACY_STORAGE_CONTRACT,
    LegacyIndexScopeWarning,
    build_index_scope_filter,
    normalize_index_scopes,
    validate_hits_in_index_scopes,
)


class LegacyEvidenceFallbackWarning(UserWarning):
    """An old point was read through the explicit source_type compatibility path."""


class QdrantRetriever:
    def __init__(
        self,
        *,
        qdrant_path: Path | None,
        qdrant_url: str | None = None,
        qdrant_api_key_env: str | None = None,
        collection_name: str,
        embedding_endpoint: str,
        embedding_model: str,
        prefer_grpc: bool = False,
        timeout: int = 60,
        index_scopes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.index_scopes = normalize_index_scopes(index_scopes, collection_name=collection_name)
        self.client = build_qdrant_client(
            qdrant_path=qdrant_path,
            qdrant_url=qdrant_url,
            api_key_env=qdrant_api_key_env,
            prefer_grpc=prefer_grpc,
            timeout=timeout,
        )
        self.collection_name = collection_name
        self.embedder = EmbeddingClient(endpoint=embedding_endpoint, model=embedding_model)
        self._qdrant_query_calls = 0

    def _scope_filter(self, namespaces: list[str]) -> models.Filter:
        scopes = getattr(self, "index_scopes", None)
        filters = build_index_scope_filter(scopes, collection_name=self.collection_name, namespaces=namespaces)
        if scopes is None or any(scope["storage_contract"] == LEGACY_STORAGE_CONTRACT and scope["namespace"] in namespaces for scope in scopes):
            warnings.warn(
                "Reading only legacy unversioned points through the explicit compatibility index scope.",
                LegacyIndexScopeWarning,
                stacklevel=3,
            )
        return filters

    @property
    def qdrant_query_calls(self) -> int:
        """Lifetime query attempts, including failed calls; safe for __new__."""
        return getattr(self, "_qdrant_query_calls", 0)

    def close(self) -> None:
        self.client.close()

    def search(
        self,
        query: str,
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        evidence_kinds: list[str] | None = None,
        required_source_types: list[str] | None = None,
        field_family_ids: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        build_index_scope_filter(getattr(self, "index_scopes", None), collection_name=self.collection_name, namespaces=namespaces)
        vector = self.embedder.embed_query(query)
        return self.search_by_vector(
            vector,
            namespaces=namespaces,
            layers=layers,
            source_types=source_types,
            evidence_kinds=evidence_kinds,
            required_source_types=required_source_types,
            field_family_ids=field_family_ids,
            top_k=top_k,
        )

    def search_field_schemas(
        self,
        query: str,
        *,
        namespaces: list[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Return auxiliary schema payloads without adapting them into evidence."""
        scope_filter = self._scope_filter(namespaces)
        vector = self.embedder.embed_query(query)
        self.last_metadata = {"retrieval_object": "field_schema", "namespaces": list(namespaces)}
        filters = models.Filter(must=[
            scope_filter,
            models.FieldCondition(key="retrieval_object", match=models.MatchValue(value="field_schema")),
        ])
        self._qdrant_query_calls = self.qdrant_query_calls + 1
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=filters,
            limit=top_k,
            with_payload=True,
        )
        hits = [
            {**(point.payload or {}), "point_id": str(point.id), "qdrant_point_id": str(point.id),
             "vector_rank": rank, "vector_score": round(float(point.score), 6)}
            for rank, point in enumerate(response.points, 1)
        ]
        validate_hits_in_index_scopes(hits, getattr(self, "index_scopes", None), collection_name=self.collection_name, namespaces=namespaces)
        return hits

    def search_by_vector(
        self,
        vector: list[float],
        *,
        namespaces: list[str],
        layers: list[str],
        source_types: list[str] | None = None,
        evidence_kinds: list[str] | None = None,
        required_source_types: list[str] | None = None,
        field_family_ids: list[str] | None = None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        scope_filter = self._scope_filter(namespaces)
        self.last_metadata = {
            "legacy_evidence_fallback_count": 0,
            "field_family_ids": list(field_family_ids) if field_family_ids is not None else None,
        }
        if evidence_kinds == [] or required_source_types == []:
            return []
        conditions: list[Any] = [
            scope_filter,
            models.FieldCondition(key="corpus_layer", match=models.MatchAny(any=layers)),
        ]
        if evidence_kinds is not None:
            # Canonical points are selected by physical kind. Source type is
            # retained solely for old points that have not been migrated.
            alternatives: list[Any] = [models.FieldCondition(key="evidence_kind", match=models.MatchAny(any=evidence_kinds))]
            if source_types:
                alternatives.append(models.Filter(must=[
                    models.IsEmptyCondition(is_empty=models.PayloadField(key="evidence_kind")),
                    models.FieldCondition(key="source_type", match=models.MatchAny(any=source_types)),
                ]))
            conditions.append(models.Filter(should=alternatives))
        elif source_types:
            conditions.append(models.FieldCondition(key="source_type", match=models.MatchAny(any=source_types)))
        if required_source_types:
            conditions.append(models.FieldCondition(key="source_type", match=models.MatchAny(any=required_source_types)))
        if field_family_ids is not None:
            non_excel = models.Filter(
                must=[models.Filter(should=[
                    models.IsEmptyCondition(is_empty=models.PayloadField(key="sheet_name")),
                    models.FieldCondition(key="sheet_name", match=models.MatchValue(value="")),
                ])],
                must_not=[models.FieldCondition(
                    key="source_type", match=models.MatchAny(any=["main_excel_capability", "uploaded_excel_row"]),
                )],
            )
            alternatives = [non_excel]
            if field_family_ids:
                alternatives.append(models.FieldCondition(key="field_family_id", match=models.MatchAny(any=field_family_ids)))
            conditions.append(models.Filter(should=alternatives))
        filters = models.Filter(
            must=conditions,
            must_not=[models.FieldCondition(key="retrieval_object", match=models.MatchValue(value="field_schema"))],
        )
        self._qdrant_query_calls = self.qdrant_query_calls + 1
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=filters,
            limit=top_k,
            with_payload=True,
        )
        hits: list[dict[str, Any]] = []
        legacy_count = 0
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
            "relative_path",
            "proof_attachments",
            "raw_source_text",
            "source_text_hash",
            "source_text_hash_space",
            "source_document_hash",
            "document_id",
            "index_version",
            "index_version_id",
            "parser_type",
            "parser_version",
        ]
        for rank, point in enumerate(response.points, 1):
            payload = point.payload or {}
            validate_hits_in_index_scopes([payload], getattr(self, "index_scopes", None), collection_name=self.collection_name, namespaces=namespaces)
            legacy = not payload.get("evidence_kind")
            payload = normalize_evidence_record(payload)
            validate_hits_in_index_scopes([payload], getattr(self, "index_scopes", None), collection_name=self.collection_name, namespaces=namespaces)
            if evidence_kinds is not None and payload.get("evidence_kind") not in evidence_kinds:
                # A legacy source type can describe multiple physical kinds.
                # Its compatibility candidate is not itself a kind assertion.
                continue
            if evidence_kinds is not None and legacy:
                legacy_count += 1
            hit = {
                **payload,
                "point_id": str(point.id),
                "qdrant_point_id": str(point.id),
                "vector_rank": rank,
                "vector_score": round(float(point.score), 6),
                "chunk_id": payload.get("chunk_id"),
                "namespace": payload.get("namespace"),
                "source_type": payload.get("source_type"),
                "corpus_layer": payload.get("corpus_layer"),
                "anchor": payload.get("anchor"),
                "file_name": payload.get("file_name"),
                "relative_path": payload.get("relative_path"),
                "raw_text": payload.get("raw_text"),
                "text_for_embedding": payload.get("text_for_embedding") or payload.get("raw_text"),
                "proof_attachment_ids": payload.get("proof_attachment_ids") or [],
                "proof_attachments": payload.get("proof_attachments") or [],
                "source": payload.get("source") or {},
            }
            for key in metadata_keys:
                if key in payload:
                    hit[key] = payload.get(key)
            hits.append(hit)
        if legacy_count:
            warnings.warn(
                f"Read {legacy_count} legacy evidence points via source_type compatibility; rebuild to persist evidence_kind.",
                LegacyEvidenceFallbackWarning,
                stacklevel=2,
            )
        self.last_metadata["legacy_evidence_fallback_count"] = legacy_count
        return hits
