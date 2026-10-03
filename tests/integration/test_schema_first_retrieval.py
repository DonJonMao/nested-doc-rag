"""Native schema/value indexing and real disk Qdrant with localhost models.

The controlled vectors deliberately rank a wrong field's value above the
correct field. This verifies family constraints and execution contracts, not
real embedding quality or a real model's semantic accuracy.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook
from qdrant_client import QdrantClient, models
from test_runtime_form_contract import (
    COLLECTION,
    GLOBAL,
    TARGET,
    Runtime,
    assert_rejected_unchanged,
    make_template,
    protected_files,
    read_json,
    read_jsonl,
)
from test_runtime_form_contract import (
    local_models as local_models,
)
from test_runtime_form_contract import (
    runtime as runtime,
)
from test_sufficiency_runtime_contract import completed, final_evidence, sufficiency_payload, validate_actual_cli

from nested_doc_rag.evidence_record import normalize_evidence_record
from nested_doc_rag.evidence_resolver import resolve_evidence_refs
from nested_doc_rag.retrieval.qdrant_retriever import QdrantRetriever

OTHER_ROOM = "schema_contract_other_room"


def run_cli(runtime: Runtime, *args: str | Path, wrapper: str | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "nested_doc_rag.cli"] if wrapper is None else [sys.executable, "-c", wrapper]
    return subprocess.run(
        [*command, *map(str, args)], cwd=runtime.root, env=runtime.env,
        capture_output=True, text=True, timeout=45, check=False,
    )


def make_native_knowledge(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "现网与规划参数"
    worksheet.append(["类别", "字段", "当前实际值", "单位"])
    for category, field_name, field_value in rows:
        worksheet.append([category, field_name, field_value, "kVA"])
    workbook.save(path)
    workbook.close()
    return path


def ingest_native_knowledge(
    runtime: Runtime, *, namespace: str, rows: list[tuple[str, str, str]], name: str,
) -> list[dict[str, Any]]:
    uploads = runtime.root / f"uploads-{name}"
    uploads.mkdir()
    make_native_knowledge(uploads / f"{name}.xlsx", rows)
    out_dir = runtime.root / f"ingested-{name}"
    result = run_cli(
        runtime, "ingest-knowledge", "--config", runtime.config, "--input-dir", uploads,
        "--namespace", namespace, "--knowledge-base-id", f"schema-contract-{name}",
        "--qdrant-collection", COLLECTION, "--out-dir", out_dir,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = read_jsonl(out_dir / "ingestion_manifest.jsonl")
    assert len(records) == len(rows) + 1
    summary = read_json(out_dir / "summary.json")
    schemas = read_jsonl(out_dir / "field_schema_manifest.jsonl")
    assert summary["record_count"] == summary["upserted_count"] == len(records)
    assert summary["schema_record_count"] == summary["schema_upserted_count"] == len(schemas)
    assert summary["total_point_count"] == summary["total_upserted_count"] == len(records) + len(schemas)
    return records


def controlled_native_index(
    runtime: Runtime, *, include_global: bool = False, include_other: bool = False, empty_capacity: bool = False,
) -> list[dict[str, Any]]:
    client = QdrantClient(path=str(runtime.qdrant))
    try:
        client.delete(COLLECTION, models.PointIdsList(points=[1]), wait=True)
    finally:
        client.close()
    records = ingest_native_knowledge(
        runtime, namespace=TARGET, name="目标机房知识",
        rows=[
            ("现网UPS", "UPS容量", "" if empty_capacity else "500kVA"),
            ("现网制冷", "空调容量", "500kVA"), ("规划UPS", "UPS规划容量", "900kVA"),
        ],
    )
    if include_global:
        records.extend(ingest_native_knowledge(
            runtime, namespace=GLOBAL, name="全局背景知识", rows=[("全局背景", "UPS容量", "1000kVA")],
        ))
    if include_other:
        records.extend(ingest_native_knowledge(
            runtime, namespace=OTHER_ROOM, name="其他机房知识", rows=[("其他机房", "UPS容量", "2000kVA")],
        ))
    client = QdrantClient(path=str(runtime.qdrant))
    try:
        points, cursor = client.scroll(COLLECTION, limit=100, with_payload=True)
        assert cursor is None
        controlled = []
        for point in points:
            payload = point.payload or {}
            if payload.get("retrieval_object") == "field_schema":
                cosine = 1.0 if payload.get("field_name") == "UPS容量" else 0.4
            elif payload.get("namespace") != TARGET:
                cosine = 1.0
            else:
                cosine = {"UPS容量": 0.6, "空调容量": 1.0, "UPS规划容量": 0.95}.get(payload.get("field_name"), 0.8)
            controlled.append(models.PointVectors(id=point.id, vector=[cosine, math.sqrt(1 - cosine**2), 0.0]))
        client.update_vectors(COLLECTION, points=controlled, wait=True)
    finally:
        client.close()
    return records


def retriever(runtime: Runtime) -> QdrantRetriever:
    return QdrantRetriever(
        qdrant_path=runtime.qdrant, collection_name=COLLECTION,
        embedding_endpoint=runtime.models.base_url + "/v1/embeddings", embedding_model="localhost-controlled-embedding",
    )


def configure_models(runtime: Runtime, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    fact = next((record for record in records if record.get("namespace") == TARGET and record.get("field_name") == "UPS容量" and record.get("field_value") == "500kVA"), None)

    def sufficiency(request: dict[str, Any]) -> dict[str, Any]:
        pack = sufficiency_payload(request)["retrieved_evidence"]
        support = [hit["evidence_id"] for hit in pack if (
            hit.get("namespace") == TARGET and hit.get("field_name") == "UPS容量"
            and hit.get("field_value") == "500kVA"
        )]
        return {
            "sufficient": bool(support), "missing_facts": [] if support else ["UPS容量"],
            "supporting_evidence_ids": support, "reason": "Explicit localhost fixture decision from current target field/value",
        }

    runtime.models.sufficiency = sufficiency
    runtime.models.answer = {
        "answer_value": "500kVA", "answer_status": "answered", "confidence": 0.95,
        "source_chunk_ids": [fact["chunk_id"]] if fact else ["must-never-be-used"],
        "reference_source_documents": [{"chunk_id": fact["chunk_id"], "quote": fact["raw_source_text"], "reason": "native target UPS field"}] if fact else [],
    }
    return fact


def schema_selection(acquisition: dict[str, Any], *, round_index: int = 0, namespace: str = TARGET) -> dict[str, Any]:
    schema = acquisition["rounds"][round_index]["schema_first"]
    assert schema["enabled"] is True
    return next(selection for selection in schema["selections"] if selection["namespace"] == namespace)


def test_native_schema_filters_higher_scoring_wrong_field_and_preserves_target_scope(runtime: Runtime) -> None:
    records = controlled_native_index(runtime, include_global=True, include_other=True)
    fact = configure_models(runtime, records)
    assert fact is not None
    schemas = read_jsonl(runtime.root / "ingested-目标机房知识" / "field_schema_manifest.jsonl")
    assert len(schemas) == 3
    current = next(schema for schema in schemas if schema["field_name"] == "UPS容量")
    assert current["field_family_id"] == fact["field_family_id"]
    assert current["sheet_name"] == "现网与规划参数" and current["category_path"] == ["现网UPS"]
    assert current["column_headers"] == ["类别", "字段", "当前实际值", "单位"] and current["unit"] == "kVA"
    query = retriever(runtime)
    try:
        broad = query.search_by_vector([1.0, 0.0, 0.0], namespaces=[TARGET], layers=["fact"], evidence_kinds=["structured_field"], top_k=20)
        assert broad[0]["field_name"] == "空调容量" and broad[0]["field_value"] == "500kVA"
        assert broad[0]["vector_score"] > next(hit["vector_score"] for hit in broad if hit["chunk_id"] == fact["chunk_id"])
    finally:
        query.close()
    template = make_template(runtime.root / "字段族约束.xlsx", sheets={"现网工勘": [(2, "UPS容量")]})
    result = runtime.run("--template", template, "--writeback", "--sufficiency-enabled", "--schema-first-enabled")
    runtime.assert_completed(result, count=1)
    prediction, overlay = completed(runtime)
    acquisition = prediction["validation"]["acquisition"]
    assert acquisition["acquisition_rounds"] == 1 and acquisition["qdrant_query_calls"] == 3
    selection = schema_selection(acquisition)
    assert selection["field_family_ids"] == [fact["field_family_id"]] and selection["fallback"] is None
    assert {selection["namespace"] for selection in acquisition["rounds"][0]["schema_first"]["selections"]} == {TARGET}
    answers = runtime.models.chat_requests(sufficiency=False)
    assert len(answers) == 1
    pack = final_evidence(answers[0])
    assert any(hit["chunk_id"] == fact["chunk_id"] for hit in pack)
    assert all(hit["namespace"] == TARGET for hit in pack)
    assert all(hit.get("field_name") not in {"空调容量", "UPS规划容量"} for hit in pack)
    assert prediction["source_chunk_ids"] == [fact["chunk_id"]] and overlay["writeback_allowed"]
    ref = prediction["evidence_refs"][0]
    assert ref["row_index"] == 2 and ref["cell_range"] == "A2:D2" and ref["source_text"] == fact["raw_source_text"]
    validate_actual_cli(runtime)


def test_auxiliary_schema_points_cannot_enter_evidence_search_or_resolve_as_sources(runtime: Runtime) -> None:
    records = controlled_native_index(runtime)
    schemas = read_jsonl(runtime.root / "ingested-目标机房知识" / "field_schema_manifest.jsonl")
    for schema in schemas:
        assert schema["retrieval_object"] == schema["corpus_layer"] == "field_schema"
        assert not ({"chunk_id", "evidence_id", "evidence_kind", "field_value", "raw_text", "raw_source_text"} & set(schema))
        with pytest.raises(ValueError, match="requires chunk_id or evidence_id"):
            normalize_evidence_record(schema)
        resolution = resolve_evidence_refs([schema["schema_id"]], schemas)
        assert not resolution.resolvable and not resolution.refs and resolution.errors
        disguised = {
            **schema, "chunk_id": schema["schema_id"], "evidence_kind": "structured_field",
            "raw_source_text": "500kVA", "row_index": 2, "cell_range": "A2:D2",
        }
        rejected = resolve_evidence_refs([schema["schema_id"]], [disguised])
        assert not rejected.resolvable and rejected.errors, "A schema marker cannot be promoted by adding evidence-shaped fields"
    query = retriever(runtime)
    try:
        # Including the auxiliary corpus layer deliberately tests the hard
        # isolation rule, beyond normal production-plan layer exclusions.
        hits = query.search_by_vector([1.0, 0.0, 0.0], namespaces=[TARGET], layers=["fact", "field_schema"], top_k=50)
        assert {hit["chunk_id"] for hit in hits} == {record["chunk_id"] for record in records}
        assert all(hit.get("retrieval_object") != "field_schema" for hit in hits)
        schema_hits = query.search_field_schemas("UPS容量", namespaces=[TARGET], top_k=10)
        assert len(schema_hits) == len(schemas) and schema_hits[0]["field_name"] == "UPS容量"
        assert query.qdrant_query_calls == 2
    finally:
        query.close()


def test_absent_schema_uses_explicit_legacy_fallback_without_fabricating_a_family(runtime: Runtime) -> None:
    records = controlled_native_index(runtime)
    configure_models(runtime, records)
    client = QdrantClient(path=str(runtime.qdrant))
    try:
        client.delete(COLLECTION, models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="retrieval_object", match=models.MatchValue(value="field_schema")),
        ])), wait=True)
    finally:
        client.close()
    template = make_template(runtime.root / "旧索引兼容.xlsx", sheets={"兼容工勘": [(2, "UPS容量")]})
    result = runtime.run("--template", template, "--sufficiency-enabled", "--schema-first-enabled")
    runtime.assert_completed(result, count=1)
    prediction, _ = completed(runtime)
    acquisition = prediction["validation"]["acquisition"]
    selection = schema_selection(acquisition)
    assert selection["fallback"] == "no_schema" and selection["field_family_ids"] is None
    assert acquisition["acquisition_rounds"] == 1 and acquisition["qdrant_query_calls"] == 3
    pack = final_evidence(runtime.models.chat_requests(sufficiency=False)[0])
    assert any(hit.get("field_name") == "空调容量" for hit in pack)
    assert any(hit.get("field_name") == "UPS容量" for hit in pack)
    validate_actual_cli(runtime)


def test_selected_empty_family_remains_constrained_through_two_acquisitions(runtime: Runtime) -> None:
    records = controlled_native_index(runtime, empty_capacity=True, include_global=True)
    configure_models(runtime, records)
    schema = next(schema for schema in read_jsonl(runtime.root / "ingested-目标机房知识" / "field_schema_manifest.jsonl") if schema["field_name"] == "UPS容量")
    template = make_template(runtime.root / "缺值不能放宽.xlsx", sheets={"现网工勘": [(2, "UPS容量")]})
    result = runtime.run("--template", template, "--writeback", "--sufficiency-enabled", "--schema-first-enabled")
    assert result.returncode == 0, result.stdout + result.stderr
    prediction, overlay = completed(runtime)
    acquisition = prediction["validation"]["acquisition"]
    assert acquisition["acquisition_rounds"] == 2 and acquisition["qdrant_query_calls"] == 10
    assert [entry["qdrant_query_calls"] for entry in acquisition["rounds"]] == [3, 7]
    for round_index in (0, 1):
        selection = schema_selection(acquisition, round_index=round_index)
        assert selection["fallback"] is None and selection["field_family_ids"] == [schema["field_family_id"]]
    checks = runtime.models.chat_requests(sufficiency=True)
    assert len(checks) == 2 and not runtime.models.chat_requests(sufficiency=False)
    for request in checks:
        evidence = sufficiency_payload(request)["retrieved_evidence"]
        assert all(hit.get("field_name") not in {"空调容量", "UPS规划容量"} for hit in evidence)
    assert prediction["answer_status"] in {"partial_clue", "not_found"} and prediction["answer_value"] == "未找到"
    assert not overlay["writeback_allowed"]
    assert read_jsonl(runtime.root / "run" / "writeback_audit.jsonl")[0]["writeback_action"] != "written"
    validate_actual_cli(runtime)


@pytest.mark.parametrize("change", ["flag", "contract"])
def test_schema_strategy_or_contract_change_rejects_resume_before_calls_and_writes(runtime: Runtime, change: str) -> None:
    records = controlled_native_index(runtime)
    configure_models(runtime, records)
    template = make_template(runtime.root / "schema恢复指纹.xlsx", sheets={"现网工勘": [(2, "UPS容量")]})
    initial = runtime.run("--template", template, "--sufficiency-enabled", "--schema-first-enabled")
    runtime.assert_completed(initial, count=1)
    output = runtime.root / "run"
    contract = read_json(output / "form_input_snapshot.json")["acquisition_contract"]
    assert contract["schema_first_enabled"] is True and contract["field_schema_contract_version"] == "field-schema-v1"
    (output / "predictions.checkpoint.jsonl").write_text("{damaged checkpoint}\n", encoding="utf-8")
    before, requests = protected_files(output), runtime.models.recorded()
    if change == "flag":
        result = runtime.run("--template", template, "--resume", "--sufficiency-enabled", "--no-schema-first-enabled")
    else:
        # Simulate only a schema contract release; production logic is intact.
        wrapper = (
            "import sys; import nested_doc_rag.retrieval.field_schema as schema; "
            "schema.FIELD_SCHEMA_CONTRACT_VERSION = 'field-schema-v2-test'; "
            "from nested_doc_rag.cli import main; main(sys.argv[1:])"
        )
        result = run_cli(
            runtime, "run-step15-agent", "--config", runtime.config, "--qdrant-path", runtime.qdrant,
            "--qdrant-collection", COLLECTION, "--target-namespace", TARGET, "--global-namespace", GLOBAL,
            "--rows", "all", "--out-dir", output, "--no-judge", "--template", template,
            "--resume", "--sufficiency-enabled", "--schema-first-enabled", wrapper=wrapper,
        )
    assert_rejected_unchanged(runtime, result, before, requests)
