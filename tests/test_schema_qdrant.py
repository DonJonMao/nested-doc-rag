"""Exercise schema/value boundaries with persistent Qdrant and ranked decoys."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import QdrantClient, models

from nested_doc_rag.retrieval.qdrant_retriever import LegacyEvidenceFallbackWarning, QdrantRetriever

TARGET = "schema_room_301"
OTHER = "schema_room_401"
GLOBAL = "schema_global"
COLLECTION = "schema_and_values"
VECTOR = [1.0, 0.0, 0.0]
MAIN = "main_excel_capability"
UPLOAD = "uploaded_excel_row"
NON_EXCEL = {"word_missing_sheet", "word_empty_sheet", "text_null_sheet"}


class FixedEmbedding:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return list(VECTOR)


def schema(schema_id: str, family: str, *, namespace: str = TARGET) -> dict[str, Any]:
    return {
        "schema_id": schema_id, "retrieval_object": "field_schema", "namespace": namespace,
        "corpus_layer": "field_schema", "source_type": "excel_field_schema",
        "field_family_id": family, "field_name": "UPS容量", "sheet_name": "能力",
        "schema_text": "字段：UPS容量。类别：现网UPS。单位：kVA",
        "text_for_embedding": "字段：UPS容量。类别：现网UPS。单位：kVA",
        "descriptor": {"category_path": ["现网UPS"], "unit": "kVA"},
    }


def evidence(chunk_id: str, family: str | None, *, source_type: str = UPLOAD, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chunk_id": chunk_id, "retrieval_object": "evidence", "namespace": TARGET,
        "corpus_layer": "fact", "source_type": source_type, "evidence_kind": "structured_field",
        "file_name": "能力.xlsx", "sheet_name": "能力", "row_index": 2, "cell_range": "B2:C2",
        "field_name": "UPS容量", "field_value": "500kVA",
        "raw_text": "UPS容量：500kVA", "raw_source_text": "UPS容量：500kVA",
    }
    if family is not None:
        payload["field_family_id"] = family
    payload.update(extra)
    return payload


@pytest.fixture
def disk_retriever(tmp_path: Path) -> Iterator[QdrantRetriever]:
    points: list[tuple[dict[str, Any], list[float]]] = [
        (schema("schema_ups", "ups"), [0.9, 0.43589, 0.0]),
        (schema("schema_empty", "empty"), VECTOR),
        (schema("schema_other_room", "ups", namespace=OTHER), VECTOR),
        (schema("schema_global", "ups", namespace=GLOBAL), VECTOR),
        # Even a mislabeled auxiliary point must never become ordinary evidence.
        ({**schema("schema_camouflage", "ups"), "corpus_layer": "fact", "source_type": MAIN,
          "evidence_kind": "structured_field", "raw_text": "schema is not an answer"}, VECTOR),
        (evidence("ups_selected", "ups"), [0.8, 0.6, 0.0]),
        (evidence("cooling_higher_score", "cooling", field_name="空调容量"), VECTOR),
        (evidence("empty_family_excel", ""), VECTOR),
        (evidence("unknown_source_with_sheet", "", source_type="unknown_origin"), VECTOR),
        (evidence("familyless_main_without_sheet", None, source_type=MAIN, sheet_name=None), VECTOR),
        (evidence("familyless_upload_without_sheet", None, sheet_name=""), VECTOR),
        (evidence("other_room_value", "ups", namespace=OTHER), VECTOR),
        (evidence("global_value", "ups", namespace=GLOBAL), VECTOR),
    ]
    for chunk_id, family in [("legacy_selected", "ups"), ("legacy_wrong_family", "cooling")]:
        legacy = evidence(chunk_id, family, source_type=MAIN)
        legacy.pop("evidence_kind")
        legacy.pop("retrieval_object")
        points.append((legacy, [0.7, 0.714143, 0.0] if family == "ups" else VECTOR))
    for chunk_id, source_type, sheet in [
        ("word_missing_sheet", "uploaded_docx_table_row", "missing"),
        ("word_empty_sheet", "uploaded_docx_paragraph", ""),
        ("text_null_sheet", "uploaded_text_chunk", None),
    ]:
        payload = evidence(chunk_id, None, source_type=source_type, evidence_kind="paragraph", sheet_name=sheet)
        if sheet == "missing":
            payload.pop("sheet_name")
        points.append((payload, VECTOR))
    path = str(tmp_path / "qdrant")
    client = QdrantClient(path=path)
    client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
    client.upsert(COLLECTION, points=[
        models.PointStruct(id=index, vector=vector, payload=payload)
        for index, (payload, vector) in enumerate(points, 1)
    ], wait=True)
    client.close()
    # Reopen the on-disk collection to exercise persisted payloads and filters.
    retriever = QdrantRetriever.__new__(QdrantRetriever)
    retriever.client = QdrantClient(path=path)
    retriever.collection_name = COLLECTION
    retriever.embedder = FixedEmbedding()
    try:
        yield retriever
    finally:
        retriever.close()


def search(retriever: QdrantRetriever, *, method: str = "vector", **options: Any) -> list[dict[str, Any]]:
    kwargs = {"namespaces": [TARGET], "layers": ["fact", "field_schema"], "top_k": 50, **options}
    if method == "text":
        return retriever.search("UPS容量", **kwargs)
    return retriever.search_by_vector(list(VECTOR), **kwargs)


def ids(hits: list[dict[str, Any]]) -> set[str]:
    return {hit["chunk_id"] for hit in hits}


def test_schema_search_is_scoped_raw_payload_with_real_query_count(disk_retriever: QdrantRetriever) -> None:
    hits = disk_retriever.search_field_schemas("UPS容量字段", namespaces=[TARGET], top_k=10)

    assert {hit["schema_id"] for hit in hits} == {"schema_ups", "schema_empty", "schema_camouflage"}
    assert all(hit["namespace"] == TARGET and hit["retrieval_object"] == "field_schema" for hit in hits)
    assert [hit["vector_rank"] for hit in hits] == [1, 2, 3]
    assert all(isinstance(hit["vector_score"], float) for hit in hits)
    plain = next(hit for hit in hits if hit["schema_id"] == "schema_ups")
    assert plain["point_id"] == plain["qdrant_point_id"] == "1"
    assert {key: value for key, value in plain.items() if key not in {"vector_rank", "vector_score", "point_id", "qdrant_point_id"}} == schema("schema_ups", "ups")
    assert not {"chunk_id", "evidence_id", "address", "raw_text", "evidence_kind"}.intersection(plain)
    assert disk_retriever.embedder.queries == ["UPS容量字段"]
    assert disk_retriever.qdrant_query_calls == 1
    # Returned schema metadata does not mutate the persisted auxiliary point.
    original = disk_retriever.client.retrieve(COLLECTION, ids=[1], with_payload=True)[0].payload
    assert original == schema("schema_ups", "ups")


@pytest.mark.parametrize("namespaces", [[OTHER], [GLOBAL], []])
def test_schema_search_never_widens_namespace(disk_retriever: QdrantRetriever, namespaces: list[str]) -> None:
    hits = disk_retriever.search_field_schemas("UPS容量", namespaces=namespaces, top_k=10)
    assert {hit["namespace"] for hit in hits} == set(namespaces)
    assert disk_retriever.qdrant_query_calls == 1


@pytest.mark.parametrize("method", ["text", "vector"])
def test_ordinary_search_always_excludes_auxiliary_schema(disk_retriever: QdrantRetriever, method: str) -> None:
    hits = search(disk_retriever, method=method)
    assert hits and all(hit.get("retrieval_object") != "field_schema" for hit in hits)
    assert {"ups_selected", "cooling_higher_score", "empty_family_excel", "legacy_wrong_family"} <= ids(hits)
    assert "other_room_value" not in ids(hits) and "global_value" not in ids(hits)
    assert disk_retriever.last_metadata["field_family_ids"] is None
    assert disk_retriever.qdrant_query_calls == 1


@pytest.mark.parametrize("method", ["text", "vector"])
def test_family_filter_beats_higher_scoring_wrong_excel_before_top_k(disk_retriever: QdrantRetriever, method: str) -> None:
    # A server-side family condition is required: post-filtering a top-1
    # unconstrained result would lose the lower-scoring correct field.
    hits = search(disk_retriever, method=method, field_family_ids=["ups"], source_types=[UPLOAD], top_k=1)
    assert ids(hits) == {"ups_selected"}
    assert hits[0]["vector_score"] == pytest.approx(0.8)
    assert disk_retriever.last_metadata["field_family_ids"] == ["ups"]


def test_selected_family_preserves_word_text_and_blocks_unknown_excel(disk_retriever: QdrantRetriever) -> None:
    hits = search(disk_retriever, field_family_ids=["ups"])
    assert ids(hits) == {"ups_selected", "legacy_selected", *NON_EXCEL}
    assert disk_retriever.last_metadata["field_family_ids"] == ["ups"]


@pytest.mark.parametrize("families", [["empty"], ["unknown"], []])
def test_empty_unknown_or_unmatched_family_never_falls_back_to_other_excel(
    disk_retriever: QdrantRetriever, families: list[str],
) -> None:
    assert ids(search(disk_retriever, field_family_ids=families)) == NON_EXCEL
    assert disk_retriever.last_metadata["field_family_ids"] == families


def test_family_union_permits_only_selected_excel_and_non_excel(disk_retriever: QdrantRetriever) -> None:
    assert ids(search(disk_retriever, field_family_ids=["ups", "cooling"])) == {
        "ups_selected", "cooling_higher_score", "legacy_selected", "legacy_wrong_family", *NON_EXCEL,
    }


def test_legacy_kind_fallback_remains_explicit_inside_family_filter(disk_retriever: QdrantRetriever) -> None:
    with pytest.warns(LegacyEvidenceFallbackWarning, match="1 legacy"):
        hits = search(disk_retriever, field_family_ids=["ups"], evidence_kinds=["structured_field"], source_types=[MAIN])
    assert ids(hits) == {"ups_selected", "legacy_selected"}
    assert disk_retriever.last_metadata == {"legacy_evidence_fallback_count": 1, "field_family_ids": ["ups"]}
    original = disk_retriever.client.retrieve(COLLECTION, ids=[14], with_payload=True)[0].payload
    assert "evidence_kind" not in original


def test_empty_kind_constraint_preserves_family_metadata_without_query(disk_retriever: QdrantRetriever) -> None:
    families = ["ups"]
    assert search(disk_retriever, evidence_kinds=[], field_family_ids=families) == []
    families.append("cooling")
    assert disk_retriever.last_metadata == {"legacy_evidence_fallback_count": 0, "field_family_ids": ["ups"]}
    assert disk_retriever.qdrant_query_calls == 0


def test_failed_schema_query_counts_the_actual_attempt(disk_retriever: QdrantRetriever) -> None:
    disk_retriever.collection_name = "missing_collection"
    with pytest.raises(ValueError, match="not found"):
        disk_retriever.search_field_schemas("UPS容量", namespaces=[TARGET], top_k=10)
    assert disk_retriever.qdrant_query_calls == 1
    assert disk_retriever.embedder.queries == ["UPS容量"]


def test_schema_and_value_queries_share_one_lifetime_counter(disk_retriever: QdrantRetriever) -> None:
    families = ["ups"]
    disk_retriever.search_field_schemas("UPS字段", namespaces=[TARGET], top_k=10)
    search(disk_retriever, method="text", field_family_ids=deepcopy(families))
    search(disk_retriever, field_family_ids=families)
    assert disk_retriever.qdrant_query_calls == 3
    assert disk_retriever.embedder.queries == ["UPS字段", "UPS容量"]
