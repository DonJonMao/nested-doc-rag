"""Generate synthetic cross-language evidence fixtures without network/model calls.

Runs the real local Qdrant filtering, Step15 runner, checkpointing, artifact
serialization and Excel writer. Embedding, reranking and answer arbitration are
deterministic test doubles. These fixtures measure contracts, not model quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from openpyxl import Workbook  # noqa: E402
from qdrant_client import models  # noqa: E402

from nested_doc_rag.agent.step15_runner import Step15AgentRunner  # noqa: E402
from nested_doc_rag.config import load_app_config  # noqa: E402
from nested_doc_rag.io import write_json, write_jsonl  # noqa: E402
from nested_doc_rag.retrieval import QdrantRetriever  # noqa: E402

NAMESPACE = "synthetic_evidence_contract"
COLLECTION = "synthetic_evidence_contract_v1"
SOURCES = [
    ("synthetic_exact", "机房😀的电源为双路市电，容量200kVA。"),
    ("synthetic_ambiguous", "UPS-A容量200kVA；UPS-B容量200kVA。"),
    ("synthetic_unmatched", "输入电压10kV。"),
]


class OfflineEmbedding:
    def embed_query(self, query: str) -> list[float]:
        del query
        return [1.0, 0.0, 0.0]


class OfflineReranker:
    def rerank(self, query: str, docs: list[str], top_n: int = 5) -> list[dict[str, Any]]:
        del query
        return [{"index": index, "relevance_score": 1.0 - index * 0.01} for index in range(min(top_n, len(docs)))]


def offline_answer(**kwargs: Any) -> dict[str, Any]:
    row = kwargs["item"]["row_index"]
    answers = {
        4: ("synthetic_exact", "双路市电", "双路市电"),
        5: ("synthetic_ambiguous", "200kVA", "200kVA"),
        6: ("synthetic_unmatched", "10kV", "输入电压 10kV"),
        7: ("synthetic_missing", "待核实", "不存在的来源"),
    }
    if row not in answers:
        return {"answer_status": "not_found", "answer_value": "未找到", "confidence": 0.0, "source_chunk_ids": [], "reference_source_documents": []}
    chunk_id, value, quote = answers[row]
    return {
        "answer_status": "partial_clue" if row == 5 else "answered",
        "answer_value": value,
        "confidence": 0.9,
        "source_chunk_ids": [chunk_id],
        "reference_source_documents": [{"chunk_id": chunk_id, "quote": quote}],
        "notes": "匿名合成资料；受控契约验证，不代表真实模型回答能力。",
    }


def generate_fixture(out_dir: Path) -> dict[str, Any]:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = out_dir / "synthetic_sources.xlsx"
    source_workbook = Workbook()
    source_sheet = source_workbook.active
    source_sheet.title = "匿名能力"
    source_sheet.append(["来源ID", "原文"])
    for chunk_id, raw in SOURCES:
        source_sheet.append([chunk_id, raw])
    source_workbook.save(source_path)

    template_path = out_dir / "synthetic_form.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "匿名工勘"
    sheet.append(["匿名合成验收；不代表真实业务准确率"])
    sheet["C3"] = "项目"
    sheet["D3"] = "答案"
    labels = ["电源路数", "UPS容量", "输入电压", "缺失来源", "未找到信息"]
    items = []
    for row, question in enumerate(labels, 4):
        sheet[f"C{row}"] = question
        items.append({"form_item_id": f"synthetic_field_{row}", "file_name": template_path.name, "sheet_name": sheet.title, "row_index": row, "target_cell": f"'{sheet.title}'!D{row}", "question_text": question, "instruction_text": "", "category_path": ["匿名合成"], "answer_example": "", "needs_evidence": False})
    workbook.save(template_path)
    write_jsonl(out_dir / "synthetic_form_items.jsonl", items)

    config = load_app_config(
        project_root=PROJECT_ROOT,
        default_config=out_dir / "unused-default-config.yaml",
        cli_overrides={
            "qdrant": {"url": "", "collection_name": COLLECTION},
            "retrieval": {"sufficiency_enabled": False},
            "agentscope": {"enabled": False, "mode": "off"},
            "grounding": {"evidence_strength_enabled": False, "field_binding_enabled": False, "field_binding_agent_enabled": False, "slot_decomposition_enabled": False, "pre_writeback_consistency_enabled": False},
            "writeback": {"allow_uncertain": False, "evidence_image_mode": "adjacent_columns"},
        },
    )
    retriever = QdrantRetriever(qdrant_path=out_dir / "local_qdrant", collection_name=COLLECTION, embedding_endpoint="offline-stub", embedding_model="offline-stub")
    try:
        if retriever.client.collection_exists(COLLECTION):
            retriever.client.delete_collection(COLLECTION)
        retriever.client.create_collection(COLLECTION, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE))
        points = []
        for row, (chunk_id, raw) in enumerate(SOURCES, 2):
            points.append(models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)),
                vector=[1.0, row * 0.01, 0.0],
                payload={"chunk_id": chunk_id, "namespace": NAMESPACE, "source_type": "main_excel_capability", "corpus_layer": "fact", "file_name": source_path.name, "anchor": f"匿名能力!B{row}", "raw_source_text": raw, "raw_text": f"合成可读来源：{raw}", "text_for_embedding": raw, "source_text_hash": "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest(), "source_document_hash": "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest(), "index_version": "synthetic-contract-v1", "source": {"document_id": "synthetic-doc", "relative_path": source_path.name, "sheet_name": "匿名能力", "cell": f"B{row}"}},
            ))
        retriever.client.upsert(COLLECTION, points=points, wait=True)
        retriever.embedder = OfflineEmbedding()
        runner = Step15AgentRunner(config=config, target_namespace=NAMESPACE, global_namespace="synthetic_global", out_dir=out_dir, retriever=retriever, reranker=OfflineReranker(), answer_caller=offline_answer, writeback_enabled=True, template_path=template_path, chat_max_retries=0, chat_model="offline-stub-not-a-model")
        runner.run(items)
    finally:
        retriever.close()

    manifest = json.loads((out_dir / "run_manifest.json").read_text())
    fixture_metadata = {"synthetic": True, "network_calls": 0, "model_calls": 0, "purpose": "跨语言定位/归档/前端高亮契约验证；不表示真实业务准确率，也不证明引用失配会阻止写回", "flags": {"embedding_stub": True, "rerank_stub": True, "answer_stub": True, "semantic_grounding_disabled": True, "agentic_replanning_disabled": True, "quote_location_changes_writeback_gate": False}, "scenarios": ["中文emoji唯一引用", "重复引用且partial_clue/uncertain需复核", "非逐字引用且answered体现定位不改变回写gate", "不存在的chunk", "未找到答案"], "evidence_summary": manifest["evidence"]["summary"], "run_id": manifest["run_id"]}
    write_json(out_dir / "effective_config.json", {"grounding": asdict(config.grounding), "agentscope": asdict(config.agentscope), "writeback": asdict(config.writeback), "retrieval": {**asdict(config.retrieval), "target_namespace": NAMESPACE, "global_namespace": "synthetic_global"}})
    write_json(out_dir / "fixture_metadata.json", fixture_metadata)
    manifest["artifacts"].update(fixture_effective_config="effective_config.json", fixture_metadata="fixture_metadata.json")
    write_json(out_dir / "run_manifest.json", manifest)
    return fixture_metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "artifacts/evaluation/evidence-contract-20261001")
    args = parser.parse_args()
    print(json.dumps(generate_fixture(args.out_dir), ensure_ascii=False, indent=2))
