from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openpyxl import Workbook
from qdrant_client import QdrantClient, models

from nested_doc_rag.config import load_app_config
from nested_doc_rag.ingestion import IngestionOptions, build_ingestion_records, run_knowledge_ingestion, upsert_records
from nested_doc_rag.io import read_json, read_jsonl
from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever
from nested_doc_rag.retrieval.version_scope import LegacyIndexScopeWarning

WORKSPACE = "10000000-0000-4000-8000-000000000001"
KB = "10000000-0000-4000-8000-000000000002"
DOCUMENT = "10000000-0000-4000-8000-000000000003"
FILE = "10000000-0000-4000-8000-000000000004"
V1 = "20000000-0000-4000-8000-000000000001"
V2 = "20000000-0000-4000-8000-000000000002"
COLLECTION = "versioned_ingestion"


class FixedEmbedding:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def save_snapshot(options: IngestionOptions, snapshot: dict[str, Any]) -> IngestionOptions:
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    options.input_snapshot_path.write_bytes(data)
    return replace(options, input_snapshot_hash=hashlib.sha256(data).hexdigest())


def build_input(tmp_path: Path, version: str = V1, *, content: bytes | None = None, filename: str = "参数.xlsx") -> tuple[IngestionOptions, dict[str, Any]]:
    root = tmp_path / version
    input_dir = root / "input"
    relative = f"{DOCUMENT}/{FILE}/{filename}"
    path = input_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "现场"
        sheet.append(["字段", "当前值"])
        sheet.append(["UPS容量", "500kVA"])
        workbook.save(path)
        workbook.close()
    else:
        path.write_bytes(content)
    snapshot = {
        "schema_version": "kb-build-input-v1", "workspace_id": WORKSPACE, "knowledge_base_id": KB,
        "index_version_id": version, "namespace": "room301", "collection": COLLECTION,
        "documents": [{"document_id": DOCUMENT, "file_id": FILE, "filename": filename,
                       "relative_path": relative, "object_key": f"private/{FILE}", "document_role": "fact",
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}],
    }
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing-config", env={})
    options = IngestionOptions(input_dir, "room301", KB, root / "out", config, qdrant_collection=COLLECTION,
                               index_version_id=version, input_snapshot_path=root / "input_snapshot.json", workspace_id=WORKSPACE)
    return save_snapshot(options, snapshot), snapshot


@pytest.fixture
def disk_qdrant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = QdrantClient(path=str(tmp_path / "persistent_qdrant"))
    monkeypatch.setattr("nested_doc_rag.ingestion.EmbeddingClient", lambda **kwargs: FixedEmbedding())
    monkeypatch.setattr("nested_doc_rag.ingestion.build_qdrant_client", lambda **kwargs: client)
    monkeypatch.setattr(client, "close", lambda: None)
    try:
        yield client
    finally:
        client._client.close()


def read_version(client: QdrantClient, version: str, *, schema: bool = False) -> list[dict[str, Any]]:
    retriever = QdrantRetriever.__new__(QdrantRetriever)
    retriever.client = client
    retriever.collection_name = COLLECTION
    retriever.embedder = FixedEmbedding()
    retriever.index_scopes = [{"collection": COLLECTION, "namespace": "room301", "knowledge_base_id": KB,
                               "index_version_id": version, "storage_contract": "versioned_v1"}]
    if schema:
        return retriever.search_field_schemas("UPS容量", namespaces=["room301"], top_k=100)
    return retriever.search_by_vector([1.0, 0.0, 0.0], namespaces=["room301"], layers=["fact"], top_k=100)


def test_frozen_snapshot_generates_exact_typed_receipt_after_real_counts_and_scoped_smoke(tmp_path: Path, disk_qdrant: QdrantClient) -> None:
    options, snapshot = build_input(tmp_path)
    summary = run_knowledge_ingestion(options)
    receipt = read_json(options.out_dir / "validation_receipt.json")
    records = read_jsonl(options.out_dir / "ingestion_manifest.jsonl")
    schemas = read_jsonl(options.out_dir / "field_schema_manifest.jsonl")

    assert receipt == {
        "schema_version": "kb-index-validation-v1", "index_version_id": V1, "knowledge_base_id": KB,
        "namespace": "room301", "collection": COLLECTION, "input_snapshot_hash": options.input_snapshot_hash,
        "expected_evidence_count": 2, "actual_evidence_count": 2, "expected_schema_count": 1,
        "actual_schema_count": 1, "document_count": 1, "source_hashes_verified": True,
        "smoke_passed": True, "validated_at": receipt["validated_at"],
    }
    assert receipt["validated_at"].endswith("Z") and summary["validation_state"] == "validated"
    assert all(row["index_version"] == row["index_version_id"] == V1 for row in [*records, *schemas])
    assert all(row["document_id"] == DOCUMENT and row["source_document_hash"] == "sha256:" + snapshot["documents"][0]["sha256"] for row in records)
    assert {item["point_id"] for item in read_version(disk_qdrant, V1)} == {row["point_id"] for row in records}
    assert {item["point_id"] for item in read_version(disk_qdrant, V1, schema=True)} == {row["point_id"] for row in schemas}
    assert read_json(options.out_dir / "run_manifest.json")["artifacts"]["validation_receipt"] == "validation_receipt.json"
    assert read_json(options.out_dir / "run_manifest.json")["artifacts"]["input_snapshot"] == "input_snapshot.json"
    assert (options.out_dir / "input_snapshot.json").read_bytes() == options.input_snapshot_path.read_bytes()


def test_v2_same_semantic_chunks_and_schema_do_not_overwrite_v1_and_candidate_retry_is_exact(tmp_path: Path, disk_qdrant: QdrantClient) -> None:
    one, _ = build_input(tmp_path, V1)
    two, _ = build_input(tmp_path, V2)
    run_knowledge_ingestion(one)
    original = read_version(disk_qdrant, V1)
    original_schema = read_version(disk_qdrant, V1, schema=True)
    run_knowledge_ingestion(two)
    current = read_version(disk_qdrant, V2)
    current_schema = read_version(disk_qdrant, V2, schema=True)

    assert {hit["chunk_id"] for hit in original} == {hit["chunk_id"] for hit in current}
    assert {hit["point_id"] for hit in original}.isdisjoint(hit["point_id"] for hit in current)
    assert original_schema[0]["field_family_id"] == current_schema[0]["field_family_id"]
    assert original_schema[0]["point_id"] != current_schema[0]["point_id"]
    disk_qdrant.upsert(COLLECTION, points=[models.PointStruct(id=str(uuid.uuid4()), vector=[1.0, 0.0, 0.0], payload={
        "namespace": "room301", "knowledge_base_id": KB, "index_version_id": V2, "retrieval_object": "stale-candidate",
    })], wait=True)
    run_knowledge_ingestion(replace(two, resume=True))
    assert read_version(disk_qdrant, V1) == original and read_version(disk_qdrant, V1, schema=True) == original_schema
    assert read_version(disk_qdrant, V2) == current and read_version(disk_qdrant, V2, schema=True) == current_schema
    assert disk_qdrant.count(COLLECTION, exact=True).count == 6


@pytest.mark.parametrize("failure", ["embedding", "upsert", "count", "smoke"])
def test_v2_build_or_validation_failure_never_changes_v1_or_produces_validated_receipt(tmp_path: Path, disk_qdrant: QdrantClient, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    one, _ = build_input(tmp_path, V1)
    two, _ = build_input(tmp_path, V2)
    run_knowledge_ingestion(one)
    original, schemas = read_version(disk_qdrant, V1), read_version(disk_qdrant, V1, schema=True)
    if failure == "embedding":
        monkeypatch.setattr(FixedEmbedding, "embed", lambda *args: (_ for _ in ()).throw(RuntimeError("embedding failure")))
    elif failure == "upsert":
        monkeypatch.setattr(disk_qdrant, "upsert", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("upsert failure")))
    elif failure == "count":
        monkeypatch.setattr(disk_qdrant, "count", lambda *args, **kwargs: SimpleNamespace(count=999))
    else:
        monkeypatch.setattr(disk_qdrant, "query_points", lambda *args, **kwargs: SimpleNamespace(points=[]))
    with pytest.raises(RuntimeError):
        run_knowledge_ingestion(two)
    monkeypatch.undo()
    assert not (two.out_dir / "validation_receipt.json").exists()
    assert read_version(disk_qdrant, V1) == original and read_version(disk_qdrant, V1, schema=True) == schemas


@pytest.mark.parametrize("field", ["workspace_id", "knowledge_base_id", "index_version_id", "namespace", "collection", "schema_version"])
def test_snapshot_scope_tampering_fails_before_model_qdrant_or_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    options, snapshot = build_input(tmp_path)
    snapshot[field] = "30000000-0000-4000-8000-000000000001" if field.endswith("_id") else "different"
    options = save_snapshot(options, snapshot)
    monkeypatch.setattr("nested_doc_rag.ingestion.EmbeddingClient", lambda **kwargs: pytest.fail("must not create embedder"))
    monkeypatch.setattr("nested_doc_rag.ingestion.build_qdrant_client", lambda **kwargs: pytest.fail("must not create Qdrant client"))
    with pytest.raises((RuntimeError, ValueError)):
        run_knowledge_ingestion(options)
    assert not options.out_dir.exists()


@pytest.mark.parametrize("field,value", [("document_id", "invalid"), ("file_id", "invalid"), ("relative_path", "../outside.txt"),
                                       ("filename", "../unsafe.txt"), ("sha256", "f" * 64), ("size_bytes", 1),
                                       ("size_bytes", True), ("object_key", ""), ("document_role", "")])
def test_frozen_document_manifest_tampering_fails_before_index_io(tmp_path: Path, field: str, value: Any) -> None:
    options, snapshot = build_input(tmp_path)
    snapshot["documents"][0][field] = value
    options = save_snapshot(options, snapshot)
    with pytest.raises((RuntimeError, ValueError)):
        run_knowledge_ingestion(options)
    assert not options.out_dir.exists()


def test_raw_snapshot_hash_does_not_accept_semantically_equal_reencoded_json(tmp_path: Path) -> None:
    options, snapshot = build_input(tmp_path)
    options.input_snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    with pytest.raises(RuntimeError, match="raw bytes hash"):
        run_knowledge_ingestion(options)
    assert not options.out_dir.exists()


@pytest.mark.parametrize("content,filename", [(b"not an xlsx zip", "broken.xlsx"), (b"", "empty.txt"), (b"not supported", "source.pdf")])
def test_parse_failures_empty_native_sources_and_unsupported_manifest_never_become_ready(tmp_path: Path, disk_qdrant: QdrantClient, content: bytes, filename: str) -> None:
    options, _ = build_input(tmp_path, content=content, filename=filename)
    with pytest.raises(RuntimeError):
        run_knowledge_ingestion(options)
    assert not (options.out_dir / "validation_receipt.json").exists()
    assert not disk_qdrant.collection_exists(COLLECTION)


def test_unlisted_source_files_are_not_parsed_into_the_frozen_candidate(tmp_path: Path, disk_qdrant: QdrantClient) -> None:
    options, _ = build_input(tmp_path)
    (options.input_dir / "newly_uploaded.txt").write_text("UNFROZEN_SOURCE_VALUE", encoding="utf-8")
    run_knowledge_ingestion(options)
    assert "UNFROZEN_SOURCE_VALUE" not in json.dumps(read_version(disk_qdrant, V1), ensure_ascii=False)


def test_source_replaced_by_symlink_is_rejected(tmp_path: Path) -> None:
    options, snapshot = build_input(tmp_path)
    path = options.input_dir / snapshot["documents"][0]["relative_path"]
    external = tmp_path / "outside.xlsx"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(RuntimeError, match="symlink"):
        run_knowledge_ingestion(options)


def test_legacy_ingestion_warns_and_never_deletes_an_existing_namespace(tmp_path: Path, disk_qdrant: QdrantClient, monkeypatch: pytest.MonkeyPatch) -> None:
    one, _ = build_input(tmp_path)
    run_knowledge_ingestion(one)
    original = read_version(disk_qdrant, V1)
    input_dir = tmp_path / "legacy"
    input_dir.mkdir()
    (input_dir / "old.txt").write_text("旧文档原文", encoding="utf-8")
    records, _ = build_ingestion_records(input_dir, namespace="room301", knowledge_base_id="legacy-kb")
    monkeypatch.setattr(disk_qdrant, "delete", lambda **kwargs: pytest.fail("legacy must not delete a namespace"))
    with pytest.warns(LegacyIndexScopeWarning):
        count, _ = upsert_records(qdrant=disk_qdrant, collection_name=COLLECTION, records=records,
                                  embedder=FixedEmbedding(), batch_size=2, namespace="room301")
    assert count == 1 and read_version(disk_qdrant, V1) == original


def test_candidate_sources_changed_during_embedding_cannot_receive_a_validation_receipt(tmp_path: Path, disk_qdrant: QdrantClient, monkeypatch: pytest.MonkeyPatch) -> None:
    options, snapshot = build_input(tmp_path)
    source = options.input_dir / snapshot["documents"][0]["relative_path"]
    original_embed = FixedEmbedding.embed

    def mutate_source(self: FixedEmbedding, texts: list[str]) -> list[list[float]]:
        source.write_bytes(source.read_bytes() + b"tampered-during-build")
        return original_embed(self, texts)

    monkeypatch.setattr(FixedEmbedding, "embed", mutate_source)
    with pytest.raises(RuntimeError, match="source size mismatch"):
        run_knowledge_ingestion(options)
    assert not (options.out_dir / "validation_receipt.json").exists()


def test_text_only_native_build_can_validate_zero_auxiliary_schema_points(tmp_path: Path, disk_qdrant: QdrantClient) -> None:
    options, _ = build_input(tmp_path, content="UPS容量为500kVA。\n原文空格  保留".encode(), filename="facts.txt")
    run_knowledge_ingestion(options)
    receipt = read_json(options.out_dir / "validation_receipt.json")
    assert receipt["expected_evidence_count"] == receipt["actual_evidence_count"] == 1
    assert receipt["expected_schema_count"] == receipt["actual_schema_count"] == 0
    assert receipt["smoke_passed"] and receipt["source_hashes_verified"]


def test_every_frozen_document_is_natively_parsed_and_smoke_validated(tmp_path: Path, disk_qdrant: QdrantClient) -> None:
    options, snapshot = build_input(tmp_path)
    doc_id, file_id = str(uuid.uuid4()), str(uuid.uuid4())
    relative = f"{doc_id}/{file_id}/second.txt"
    path = options.input_dir / relative
    path.parent.mkdir(parents=True)
    path.write_text("不同源文档原文\n500kVA", encoding="utf-8")
    snapshot["documents"].append({"document_id": doc_id, "file_id": file_id, "filename": "second.txt", "relative_path": relative,
                                  "object_key": f"private/{file_id}", "document_role": "fact",
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size})
    options = save_snapshot(options, snapshot)
    run_knowledge_ingestion(options)
    receipt = read_json(options.out_dir / "validation_receipt.json")
    assert receipt["document_count"] == 2 and receipt["expected_evidence_count"] == 3
    assert {hit["document_id"] for hit in read_version(disk_qdrant, V1)} == {DOCUMENT, doc_id}


def test_failed_candidate_retry_cannot_leave_a_stale_success_receipt(tmp_path: Path, disk_qdrant: QdrantClient) -> None:
    options, snapshot = build_input(tmp_path)
    run_knowledge_ingestion(options)
    source = options.input_dir / snapshot["documents"][0]["relative_path"]
    source.write_bytes(source.read_bytes() + b"changed-after-validation")
    with pytest.raises(RuntimeError, match="source size mismatch"):
        run_knowledge_ingestion(replace(options, resume=True))
    assert not (options.out_dir / "validation_receipt.json").exists()


def test_snapshot_changed_during_embedding_cannot_be_archived_under_the_original_hash(tmp_path: Path, disk_qdrant: QdrantClient, monkeypatch: pytest.MonkeyPatch) -> None:
    options, _ = build_input(tmp_path)
    original_embed = FixedEmbedding.embed

    def mutate_snapshot(self: FixedEmbedding, texts: list[str]) -> list[list[float]]:
        options.input_snapshot_path.write_bytes(options.input_snapshot_path.read_bytes() + b"\n")
        return original_embed(self, texts)

    monkeypatch.setattr(FixedEmbedding, "embed", mutate_snapshot)
    with pytest.raises(RuntimeError, match="snapshot changed"):
        run_knowledge_ingestion(options)
    assert not (options.out_dir / "validation_receipt.json").exists()
    assert not (options.out_dir / "input_snapshot.json").exists()
