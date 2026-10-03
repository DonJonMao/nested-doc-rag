"""Real local-Qdrant contract tests; model scores are deliberately irrelevant.

All points share a vector so incorrect scope/kind/source filters cannot hide
behind ranking. Only embedding and reranking are deterministic test doubles.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from typing import Any

import pytest
from qdrant_client import QdrantClient, models

from nested_doc_rag.retrieval.layered import (
    constrain_layered_plan,
    filter_hits_by_plan,
    layered_rerank_hits,
)
from nested_doc_rag.retrieval.qdrant_retriever import (
    LegacyEvidenceFallbackWarning,
    QdrantRetriever,
)

TARGET = "uploaded_room_301"
OTHER_ROOM = "uploaded_room_401"
GLOBAL = "global"
VECTOR = [1.0, 0.0, 0.0]
MAIN = "main_excel_capability"
UPLOAD = "uploaded_excel_row"
WORD = "uploaded_docx_table_row"


class FixedEmbedding:
    def embed_query(self, query: str) -> list[float]:
        del query
        return list(VECTOR)


class FixedReranker:
    def rerank(self, query: str, docs: list[str], *, top_n: int) -> list[dict[str, Any]]:
        del query
        return [{"index": i, "relevance_score": 1.0} for i in range(min(top_n, len(docs)))]


def payload(
    chunk_id: str,
    *,
    source_type: str,
    kind: str | None,
    namespace: str = TARGET,
    layer: str = "fact",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "chunk_id": chunk_id,
        "namespace": namespace,
        "knowledge_base_id": f"kb_{namespace}",
        "source_type": source_type,
        "corpus_layer": layer,
        "file_name": "匿名知识.xlsx",
        "relative_path": "匿名知识.xlsx",
        "sheet_name": "能力",
        "row_index": 2,
        "cell_range": "A2:C2",
        "raw_text": "UPS资料：容量500kVA；备注需核对。",
    }
    if kind is not None:
        record["evidence_kind"] = kind
    if kind == "structured_field":
        record.update(field_name="UPS容量", field_value="500kVA")
    return record


@pytest.fixture
def local_corpus() -> Iterator[tuple[QdrantRetriever, list[dict[str, Any]]]]:
    records = [
        payload("canonical_uploaded", source_type=UPLOAD, kind="structured_field"),
        payload("canonical_main", source_type=MAIN, kind="structured_field"),
        payload("canonical_word", source_type=WORD, kind="structured_field"),
        payload("generic_uploaded", source_type=UPLOAD, kind="table_row"),
        # A historical source label cannot override an explicit physical kind.
        payload("generic_with_main_label", source_type=MAIN, kind="table_row"),
        payload("legacy_main", source_type=MAIN, kind=None),
        payload("legacy_generic_uploaded", source_type=UPLOAD, kind=None),
        payload("legacy_word_row", source_type=WORD, kind=None),
        payload("other_room", source_type=UPLOAD, kind="structured_field", namespace=OTHER_ROOM),
        payload("other_room_legacy", source_type=MAIN, kind=None, namespace=OTHER_ROOM),
        payload("global_fact", source_type=UPLOAD, kind="structured_field", namespace=GLOBAL),
        payload("wrong_layer", source_type=UPLOAD, kind="structured_field", layer="raw_text"),
        payload("canonical_unknown_origin", source_type="unknown_origin", kind="document_chunk"),
        payload("legacy_unknown_origin", source_type="unknown_origin", kind=None),
        payload("legacy_text", source_type="uploaded_text_chunk", kind=None),
    ]
    client = QdrantClient(":memory:")
    client.create_collection(
        "evidence_contract", vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE)
    )
    client.upsert(
        "evidence_contract",
        points=[models.PointStruct(id=i, vector=list(VECTOR), payload=record) for i, record in enumerate(records, 1)],
        wait=True,
    )
    retriever = QdrantRetriever.__new__(QdrantRetriever)
    retriever.client = client
    retriever.collection_name = "evidence_contract"
    retriever.embedder = FixedEmbedding()
    try:
        yield retriever, records
    finally:
        retriever.close()


def chunk_ids(hits: list[dict[str, Any]]) -> set[str]:
    return {hit["chunk_id"] for hit in hits}


def search(retriever: QdrantRetriever, method: str = "vector", **overrides: Any) -> list[dict[str, Any]]:
    options = {
        "namespaces": [TARGET],
        "layers": ["fact"],
        "evidence_kinds": ["structured_field"],
        "source_types": [MAIN],
        "top_k": 50,
        **overrides,
    }
    if method == "text":
        return retriever.search("UPS容量", **options)
    return retriever.search_by_vector(list(VECTOR), **options)


def structured_plan(**overrides: Any) -> list[dict[str, Any]]:
    return [{
        "layer_name": "target_structured_fact",
        "description": "目标结构化事实",
        "namespaces": "target",
        "corpus_layers": ["fact"],
        "evidence_kinds": ["structured_field"],
        "source_types": [MAIN],
        "vector_top_k": 50,
        "rerank_top_n": 50,
        **overrides,
    }]


@pytest.mark.parametrize("method", ["vector", "text"])
def test_canonical_kind_overrides_legacy_source_allowlist(local_corpus, method: str) -> None:
    retriever, _ = local_corpus
    with pytest.warns(LegacyEvidenceFallbackWarning, match="1 legacy"):
        hits = search(retriever, method)

    assert chunk_ids(hits) == {"canonical_uploaded", "canonical_main", "canonical_word", "legacy_main"}
    assert {hit["evidence_kind"] for hit in hits} == {"structured_field"}
    assert {hit["namespace"] for hit in hits} == {TARGET}
    assert {hit["corpus_layer"] for hit in hits} == {"fact"}


def test_legacy_fallback_requires_explicit_allowlist_and_reports_it(local_corpus) -> None:
    retriever, _ = local_corpus
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        canonical_hits = search(retriever, source_types=None)
    assert chunk_ids(canonical_hits) == {"canonical_uploaded", "canonical_main", "canonical_word"}
    assert not any(issubclass(notice.category, LegacyEvidenceFallbackWarning) for notice in notices)
    assert retriever.last_metadata["legacy_evidence_fallback_count"] == 0

    with pytest.warns(LegacyEvidenceFallbackWarning, match="1 legacy"):
        compatible_hits = search(retriever)
    assert chunk_ids(compatible_hits) - chunk_ids(canonical_hits) == {"legacy_main"}
    assert retriever.last_metadata["legacy_evidence_fallback_count"] == 1
    legacy = next(hit for hit in compatible_hits if hit["chunk_id"] == "legacy_main")
    assert legacy["evidence_kind"] == "structured_field"
    assert legacy["source_type"] == MAIN
    assert legacy["address"]["cell_range"] == "A2:C2"

    # Read compatibility must not silently migrate or rewrite the stored point.
    stored, _ = retriever.client.scroll("evidence_contract", limit=50, with_payload=True)
    original = next(point.payload for point in stored if point.payload["chunk_id"] == "legacy_main")
    assert "evidence_kind" not in original


@pytest.mark.parametrize("method", ["vector", "text"])
@pytest.mark.parametrize(
    ("required_source", "expected"),
    [
        (MAIN, {"canonical_main", "generic_with_main_label", "legacy_main"}),
        (UPLOAD, {"canonical_uploaded", "generic_uploaded", "legacy_generic_uploaded"}),
        (WORD, {"canonical_word", "legacy_word_row"}),
    ],
)
def test_explicit_source_constraint_applies_to_new_and_old_points(local_corpus, method, required_source, expected) -> None:
    retriever, _ = local_corpus
    with pytest.warns(LegacyEvidenceFallbackWarning, match="1 legacy"):
        hits = search(
            retriever,
            method,
            evidence_kinds=["structured_field", "table_row"],
            source_types=[MAIN, UPLOAD, WORD],
            required_source_types=[required_source],
        )
    assert chunk_ids(hits) == expected
    assert {hit["source_type"] for hit in hits} == {required_source}


@pytest.mark.parametrize(
    ("namespaces", "expected"),
    [
        ([TARGET], {"canonical_uploaded", "canonical_main", "canonical_word"}),
        ([OTHER_ROOM], {"other_room"}),
        ([GLOBAL], {"global_fact"}),
    ],
)
def test_namespace_isolation_is_not_changed_by_canonical_selection(local_corpus, namespaces, expected) -> None:
    retriever, _ = local_corpus
    assert chunk_ids(search(retriever, namespaces=namespaces, source_types=None)) == expected


@pytest.mark.parametrize("constraint", ["evidence_kinds", "required_source_types", "namespaces", "layers"])
def test_empty_explicit_constraint_cannot_widen_a_qdrant_search(local_corpus, constraint: str) -> None:
    retriever, _ = local_corpus
    assert search(retriever, **{constraint: []}) == []


def test_generic_uploaded_rows_cannot_enter_structured_retrieval_via_legacy_fallback(local_corpus) -> None:
    retriever, _ = local_corpus
    with pytest.warns(LegacyEvidenceFallbackWarning):
        hits = search(retriever, source_types=[MAIN, UPLOAD])
    assert chunk_ids(hits) == {"canonical_uploaded", "canonical_main", "canonical_word", "legacy_main"}, (
        "A generic legacy uploaded row must not be promoted into structured-field retrieval merely by its source label."
    )
    assert all(hit["evidence_kind"] == "structured_field" for hit in hits)

    with pytest.warns(LegacyEvidenceFallbackWarning):
        table_hits = search(retriever, evidence_kinds=["table_row"], source_types=[UPLOAD])
    assert chunk_ids(table_hits) == {"generic_uploaded", "generic_with_main_label", "legacy_generic_uploaded"}
    assert all(hit["evidence_kind"] == "table_row" for hit in table_hits)


@pytest.mark.parametrize("required_source", [None, MAIN])
def test_injected_and_real_layered_retrieval_share_kind_and_source_semantics(local_corpus, required_source) -> None:
    retriever, records = local_corpus
    plan = structured_plan()
    expected = {"canonical_uploaded", "canonical_main", "canonical_word", "legacy_main"}
    if required_source is not None:
        plan = constrain_layered_plan(plan, source_types=[required_source])
        expected = {"canonical_main", "legacy_main"}

    with pytest.warns(LegacyEvidenceFallbackWarning):
        reranked, vector_hits = layered_rerank_hits(
            "UPS容量",
            retriever=retriever,
            target_namespace=TARGET,
            global_namespace=GLOBAL,
            allowed_layers=["fact"],
            reranker=FixedReranker(),
            layered_plan=plan,
        )
    injected = filter_hits_by_plan(
        records,
        layered_plan=plan,
        target_namespace=TARGET,
        global_namespace=GLOBAL,
        allowed_layers=["fact"],
    )
    assert chunk_ids(vector_hits) == expected
    assert chunk_ids(reranked) == expected
    assert chunk_ids(injected) == expected


@pytest.mark.parametrize("constraint", ["evidence_kinds", "required_source_types"])
@pytest.mark.parametrize("backend", ["layered", "injected"])
def test_empty_constraint_stays_empty_in_layered_and_injected_backends(local_corpus, constraint, backend) -> None:
    retriever, records = local_corpus
    plan = structured_plan(**{constraint: []})
    if backend == "injected":
        hits = filter_hits_by_plan(
            records, layered_plan=plan, target_namespace=TARGET, global_namespace=GLOBAL, allowed_layers=["fact"]
        )
    else:
        hits, vector_hits = layered_rerank_hits(
            "UPS容量",
            retriever=retriever,
            target_namespace=TARGET,
            global_namespace=GLOBAL,
            allowed_layers=["fact"],
            reranker=FixedReranker(),
            layered_plan=plan,
        )
        assert vector_hits == []
    assert hits == []


def test_unknown_legacy_origin_is_not_an_implicit_fallback_in_any_backend(local_corpus) -> None:
    retriever, records = local_corpus
    plan = structured_plan(evidence_kinds=["document_chunk"], source_types=["uploaded_text_chunk"])
    # Both legacy records normalize to document_chunk. Only the registered
    # fallback origin is eligible; an explicit canonical kind remains valid.
    expected = {"canonical_unknown_origin", "legacy_text"}
    with pytest.warns(LegacyEvidenceFallbackWarning, match="1 legacy"):
        reranked, vector_hits = layered_rerank_hits(
            "UPS容量",
            retriever=retriever,
            target_namespace=TARGET,
            global_namespace=GLOBAL,
            allowed_layers=["fact"],
            reranker=FixedReranker(),
            layered_plan=plan,
        )
    injected = filter_hits_by_plan(
        records, layered_plan=plan, target_namespace=TARGET, global_namespace=GLOBAL, allowed_layers=["fact"]
    )
    assert chunk_ids(reranked) == expected
    assert chunk_ids(vector_hits) == expected
    assert chunk_ids(injected) == expected


def test_kind_only_plan_excludes_legacy_points_without_a_fallback_allowlist(local_corpus) -> None:
    retriever, records = local_corpus
    plan = structured_plan()
    del plan[0]["source_types"]
    expected = {"canonical_uploaded", "canonical_main", "canonical_word"}
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        reranked, vector_hits = layered_rerank_hits(
            "UPS容量",
            retriever=retriever,
            target_namespace=TARGET,
            global_namespace=GLOBAL,
            allowed_layers=["fact"],
            reranker=FixedReranker(),
            layered_plan=plan,
        )
        injected = filter_hits_by_plan(
            records, layered_plan=plan, target_namespace=TARGET, global_namespace=GLOBAL, allowed_layers=["fact"]
        )
    assert chunk_ids(reranked) == expected
    assert chunk_ids(vector_hits) == expected
    assert chunk_ids(injected) == expected
    assert not any(issubclass(notice.category, LegacyEvidenceFallbackWarning) for notice in notices)
