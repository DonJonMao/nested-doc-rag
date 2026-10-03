from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient, models

from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever
from nested_doc_rag.retrieval.version_scope import (
    IndexScopeError,
    LegacyIndexScopeWarning,
    normalize_index_scopes,
    validate_hits_in_index_scopes,
    versioned_point_id,
)

KB1 = "10000000-0000-4000-8000-000000000001"
KB2 = "10000000-0000-4000-8000-000000000002"
V1 = "20000000-0000-4000-8000-000000000001"
V2 = "20000000-0000-4000-8000-000000000002"
COLLECTION = "version_scope_contract"
VECTOR = [1.0, 0.0, 0.0]


def scope(namespace: str = "target", kb: str = KB1, version: str = V1, **extra: Any) -> dict[str, Any]:
    return {"collection": COLLECTION, "namespace": namespace, "knowledge_base_id": kb,
            "index_version_id": version, "storage_contract": "versioned_v1", **extra}


class FixedEmbedding:
    def __init__(self) -> None:
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return list(VECTOR)


def hit(chunk: str, pin: dict[str, Any], *, schema: bool = False, **extra: Any) -> dict[str, Any]:
    common = {key: pin[key] for key in ("namespace", "knowledge_base_id", "index_version_id")}
    common.update({"index_version": pin["index_version_id"], **extra})
    if schema:
        return {"schema_id": chunk, "field_family_id": "family-ups", "schema_text": "字段：UPS容量",
                "retrieval_object": "field_schema", "corpus_layer": "field_schema", **common}
    return {"chunk_id": chunk, "retrieval_object": "evidence", "corpus_layer": "fact",
            "evidence_kind": "paragraph", "source_type": "uploaded_text_chunk", "file_name": "source.txt",
            "raw_source_text": "UPS容量500kVA", "raw_text": "UPS容量500kVA", **common}


def retriever_for(client: QdrantClient, scopes: list[dict[str, Any]] | None) -> QdrantRetriever:
    retriever = QdrantRetriever.__new__(QdrantRetriever)
    retriever.client = client
    retriever.collection_name = COLLECTION
    retriever.embedder = FixedEmbedding()
    retriever.index_scopes = scopes
    return retriever


@pytest.mark.parametrize("schema_search", [False, True])
def test_exact_scope_pairs_filter_cross_products_before_top_k_and_return_actual_point_ids(tmp_path: Path, schema_search: bool) -> None:
    pins = [scope(), scope("global", KB2, V2)]
    entries = [
        (hit("selected", pins[0], schema=schema_search, point_id="forged-semantic-id", qdrant_point_id="forged-id"), [0.8, 0.6, 0.0]),
        (hit("global-selected", pins[1], schema=schema_search), VECTOR),
        (hit("cross-version", scope(version=V2), schema=schema_search), VECTOR),
        (hit("cross-kb", scope(kb=KB2), schema=schema_search), VECTOR),
        (hit("cross-both", scope(kb=KB2, version=V2), schema=schema_search), VECTOR),
        (hit("legacy", scope(), schema=schema_search, index_version_id=None, index_version="legacy"), VECTOR),
    ]
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    actual_ids = [str(uuid.uuid4()) for _ in entries]
    client.upsert(COLLECTION, points=[models.PointStruct(id=point_id, vector=vector, payload=payload)
                                    for point_id, (payload, vector) in zip(actual_ids, entries, strict=True)], wait=True)
    client.close()
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    retriever = retriever_for(client, pins)
    try:
        if schema_search:
            selected = retriever.search_field_schemas("UPS容量", namespaces=["target"], top_k=1)
        else:
            selected = retriever.search("UPS容量", namespaces=["target"], layers=["fact"], top_k=1)
        assert selected[0]["point_id"] == selected[0]["qdrant_point_id"] == actual_ids[0]
        assert selected[0]["vector_score"] == pytest.approx(0.8)
        if schema_search:
            combined = retriever.search_field_schemas("UPS容量", namespaces=["target", "global"], top_k=20)
            assert {item["schema_id"] for item in combined} == {"selected", "global-selected"}
        else:
            combined = retriever.search_by_vector(VECTOR, namespaces=["target", "global"], layers=["fact"], top_k=20)
            assert {item["chunk_id"] for item in combined} == {"selected", "global-selected"}
    finally:
        retriever.close()


@pytest.mark.parametrize("method", ["text", "vector", "schema"])
def test_requested_namespace_without_pin_fails_before_embedding_or_query(method: str) -> None:
    retriever = retriever_for(None, [scope()])
    with pytest.raises(IndexScopeError, match="no pinned"):
        if method == "schema":
            retriever.search_field_schemas("query", namespaces=["foreign"], top_k=1)
        elif method == "text":
            retriever.search("query", namespaces=["foreign"], layers=["fact"], top_k=1)
        else:
            retriever.search_by_vector(VECTOR, namespaces=["foreign"], layers=["fact"], top_k=1)
    assert retriever.embedder.calls == 0 and retriever.qdrant_query_calls == 0


@pytest.mark.parametrize("pins", [[], [scope(), scope(version=V2)], [scope(), scope("global", KB2, V2, collection="other")],
                                 [scope(knowledge_base_id="not-uuid")], [scope(index_version_id=str(uuid.UUID(int=0)))],
                                 [scope(storage_contract="unknown")]])
def test_invalid_pins_fail_closed(pins: list[dict[str, Any]]) -> None:
    with pytest.raises(IndexScopeError):
        normalize_index_scopes(pins, collection_name=COLLECTION)


@pytest.mark.parametrize("change", [{"namespace": "global"}, {"knowledge_base_id": KB2}, {"index_version_id": V2},
                                   {"index_version": V2}, {"collection": "other"}, {"source": {"index_version_id": V2}},
                                   {"source": {"knowledge_base_id": KB2}}])
def test_injected_pack_must_match_one_entire_frozen_scope(change: dict[str, Any]) -> None:
    with pytest.raises(IndexScopeError):
        validate_hits_in_index_scopes([hit("candidate", scope(), **change)], [scope()], collection_name=COLLECTION)


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("schema_search", [False, True])
def test_legacy_compatibility_is_observable_and_never_reads_versioned_points(tmp_path: Path, pinned: bool, schema_search: bool) -> None:
    entries = [hit("legacy", scope(), schema=schema_search, index_version_id=None, index_version="historical"),
               hit("new", scope(), schema=schema_search),
               hit("legacy-other-kb", scope(kb=KB2), schema=schema_search, index_version_id=None)]
    client = QdrantClient(path=str(tmp_path / "legacy-qdrant"))
    client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    client.upsert(COLLECTION, points=[models.PointStruct(id=index, vector=VECTOR, payload=payload)
                                    for index, payload in enumerate(entries, 1)], wait=True)
    retriever = retriever_for(client, [scope(storage_contract="legacy_unversioned")] if pinned else None)
    try:
        with pytest.warns(LegacyIndexScopeWarning):
            if schema_search:
                hits = retriever.search_field_schemas("query", namespaces=["target"], top_k=10)
                selected = {item["schema_id"] for item in hits}
            else:
                hits = retriever.search_by_vector(VECTOR, namespaces=["target"], layers=["fact"], top_k=10)
                selected = {item["chunk_id"] for item in hits}
        assert selected == ({"legacy"} if pinned else {"legacy", "legacy-other-kb"})
        assert "new" not in selected
    finally:
        retriever.close()


def test_normalization_is_detached_and_version_point_identity_is_stable_by_kind_and_version() -> None:
    original = [scope()]
    expected = deepcopy(original)
    normalized = normalize_index_scopes(original)
    normalized[0]["namespace"] = "changed"
    assert original == expected
    assert versioned_point_id(V1, "same") == versioned_point_id(V1, "same")
    assert len({versioned_point_id(V1, "same"), versioned_point_id(V2, "same"), versioned_point_id(V1, "same", object_kind="field_schema")}) == 3


@pytest.mark.parametrize("location", ["source", "metadata"])
def test_legacy_injected_pack_cannot_hide_a_version_claim_in_its_source(location: str) -> None:
    payload = hit("hidden-version", scope(), index_version_id=None, **{location: {"index_version_id": V1}})
    for pins in (None, [scope(storage_contract="legacy_unversioned")]):
        with pytest.raises(IndexScopeError):
            validate_hits_in_index_scopes([payload], pins, collection_name=COLLECTION)


@pytest.mark.parametrize("metadata", [{"index_version_id": V1}, {"source": {"index_version_id": V1}}])
def test_legacy_disk_payload_cannot_promote_a_hidden_metadata_version(tmp_path: Path, metadata: dict[str, Any]) -> None:
    client = QdrantClient(path=str(tmp_path / "hidden-version-qdrant"))
    client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    payload = hit("hidden-version", scope(), index_version_id=None, metadata=metadata)
    client.upsert(COLLECTION, points=[models.PointStruct(id=1, vector=VECTOR, payload=payload)], wait=True)
    retriever = retriever_for(client, [scope(storage_contract="legacy_unversioned")])
    try:
        with pytest.warns(LegacyIndexScopeWarning), pytest.raises(IndexScopeError):
            retriever.search_by_vector(VECTOR, namespaces=["target"], layers=["fact"], top_k=1)
    finally:
        retriever.close()
