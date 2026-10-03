"""Exact immutable scopes shared by Qdrant queries and injected evidence packs."""
from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from qdrant_client import models

VERSIONED_STORAGE_CONTRACT = "versioned_v1"
LEGACY_STORAGE_CONTRACT = "legacy_unversioned"


class IndexScopeError(ValueError):
    """A query or evidence pack violates its frozen index scopes."""


class LegacyIndexScopeWarning(UserWarning):
    """The explicit compatibility path can only read unversioned points."""


def canonical_uuid(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise IndexScopeError(f"{name} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
        if parsed.int == 0:
            raise ValueError("nil UUID")
        return str(parsed)
    except (ValueError, AttributeError) as exc:
        raise IndexScopeError(f"{name} must be a UUID string") from exc


def versioned_point_id(index_version_id: str, semantic_id: str, *, object_kind: str = "evidence") -> str:
    version = canonical_uuid(index_version_id, name="index_version_id")
    if not isinstance(semantic_id, str) or not semantic_id:
        raise IndexScopeError("semantic_id must be nonempty")
    if object_kind not in {"evidence", "field_schema"}:
        raise IndexScopeError("unsupported versioned point object kind")
    return str(uuid.uuid5(uuid.UUID(version), f"{object_kind}|{semantic_id}"))


def normalize_index_scopes(
    index_scopes: Iterable[Mapping[str, Any]] | None,
    *,
    collection_name: str | None = None,
    namespaces: list[str] | None = None,
) -> list[dict[str, Any]] | None:
    if index_scopes is None:
        return None
    if isinstance(index_scopes, (str, bytes, Mapping)):
        raise IndexScopeError("index_scopes must be a nonempty list of scope objects")
    scopes: list[dict[str, Any]] = []
    seen_namespaces: set[str] = set()
    for raw in index_scopes:
        if not isinstance(raw, Mapping):
            raise IndexScopeError("each index scope must be an object")
        scope = dict(raw)
        for key in ("collection", "namespace"):
            value = scope.get(key)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise IndexScopeError(f"index scope {key} must be a nonempty string without surrounding whitespace")
        if scope["namespace"] in seen_namespaces:
            raise IndexScopeError("each requested namespace must have exactly one pinned KB/version")
        seen_namespaces.add(scope["namespace"])
        scope["knowledge_base_id"] = canonical_uuid(scope.get("knowledge_base_id"), name="knowledge_base_id")
        scope["index_version_id"] = canonical_uuid(scope.get("index_version_id"), name="index_version_id")
        if scope.get("storage_contract") not in {VERSIONED_STORAGE_CONTRACT, LEGACY_STORAGE_CONTRACT}:
            raise IndexScopeError("unsupported index storage_contract")
        scopes.append(scope)
    if not scopes:
        raise IndexScopeError("index_scopes cannot be empty")
    collections = {scope["collection"] for scope in scopes}
    if len(collections) != 1 or (collection_name is not None and collections != {collection_name}):
        raise IndexScopeError("index scopes require the retriever's single collection; different collections are unsupported")
    if namespaces is not None and set(namespaces) - seen_namespaces:
        raise IndexScopeError("requested namespace has no pinned index scope")
    return scopes


def build_index_scope_filter(
    index_scopes: Iterable[Mapping[str, Any]] | None,
    *,
    collection_name: str,
    namespaces: list[str],
) -> models.Filter:
    scopes = normalize_index_scopes(index_scopes, collection_name=collection_name, namespaces=namespaces)
    if scopes is None:
        return models.Filter(must=[
            models.FieldCondition(key="namespace", match=models.MatchAny(any=namespaces)),
            models.IsEmptyCondition(is_empty=models.PayloadField(key="index_version_id")),
        ])
    alternatives: list[Any] = []
    for scope in scopes:
        if scope["namespace"] not in namespaces:
            continue
        conditions: list[Any] = [
            models.FieldCondition(key="namespace", match=models.MatchValue(value=scope["namespace"])),
            models.FieldCondition(key="knowledge_base_id", match=models.MatchValue(value=scope["knowledge_base_id"])),
        ]
        if scope["storage_contract"] == VERSIONED_STORAGE_CONTRACT:
            conditions.append(models.FieldCondition(key="index_version_id", match=models.MatchValue(value=scope["index_version_id"])))
        else:
            conditions.append(models.IsEmptyCondition(is_empty=models.PayloadField(key="index_version_id")))
        alternatives.append(models.Filter(must=conditions))
    if not alternatives:
        # An empty requested namespace list is an empty query, never all pins.
        return models.Filter(must=[models.FieldCondition(key="namespace", match=models.MatchAny(any=[]))])
    return models.Filter(should=alternatives)


def validate_hits_in_index_scopes(
    hits: Iterable[Mapping[str, Any]],
    index_scopes: Iterable[Mapping[str, Any]] | None,
    *,
    collection_name: str,
    namespaces: list[str] | None = None,
) -> None:
    scopes = normalize_index_scopes(index_scopes, collection_name=collection_name, namespaces=namespaces)
    for hit in hits:
        if not isinstance(hit, Mapping):
            raise IndexScopeError("retrieval hit must be an object")
        if namespaces is not None and hit.get("namespace") not in namespaces:
            raise IndexScopeError("retrieval hit namespace is outside the requested scopes")
        if scopes is None:
            if hit.get("index_version_id") not in (None, ""):
                raise IndexScopeError("unversioned compatibility retrieval cannot read versioned evidence")
            for location in ("source", "metadata"):
                nested = hit.get(location)
                if isinstance(nested, Mapping) and nested.get("index_version_id") not in (None, ""):
                    raise IndexScopeError("unversioned compatibility metadata cannot claim versioned evidence")
            continue
        matching = [scope for scope in scopes if scope["namespace"] == hit.get("namespace")
                    and scope["knowledge_base_id"] == hit.get("knowledge_base_id")]
        if not matching:
            raise IndexScopeError("retrieval hit KB/namespace is outside the frozen index scopes")
        scope = matching[0]
        if scope["storage_contract"] == LEGACY_STORAGE_CONTRACT:
            if hit.get("index_version_id") not in (None, ""):
                raise IndexScopeError("legacy index scope cannot read versioned evidence")
        elif hit.get("index_version_id") != scope["index_version_id"]:
            raise IndexScopeError("retrieval hit version is outside the frozen index scope")
        elif hit.get("index_version") not in (None, scope["index_version_id"]):
            raise IndexScopeError("retrieval hit index_version alias disagrees with its UUID")
        for location in ("source", "metadata"):
            source = hit.get(location)
            if not isinstance(source, Mapping):
                continue
            for key in ("knowledge_base_id", "namespace"):
                if source.get(key) is not None and source[key] != scope[key]:
                    raise IndexScopeError("retrieval hit source disagrees with its frozen KB/namespace")
            if scope["storage_contract"] == LEGACY_STORAGE_CONTRACT:
                if source.get("index_version_id") not in (None, ""):
                    raise IndexScopeError("legacy source cannot claim versioned evidence")
            else:
                for key in ("index_version_id", "index_version"):
                    if source.get(key) is not None and source[key] != scope["index_version_id"]:
                        raise IndexScopeError("retrieval hit source version disagrees with its frozen UUID")
        for key in ("collection", "collection_name", "qdrant_collection"):
            if hit.get(key) is not None and hit[key] != collection_name:
                raise IndexScopeError("retrieval hit collection disagrees with its frozen scope")
