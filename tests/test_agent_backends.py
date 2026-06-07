from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from nested_doc_rag.agent.backends import DeterministicAnswerGenerator, MiniCorpusRetriever
from nested_doc_rag.agent.runner import FieldFillingAgent
from nested_doc_rag.io import read_jsonl
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction


def load_mini_golds() -> list[FieldGold]:
    root = Path(__file__).resolve().parents[1]
    return [FieldGold.from_dict(record) for record in read_jsonl(root / "examples/mini_data/gold_fields.jsonl")]


def load_mini_corpus() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    return read_jsonl(root / "examples/mini_data/knowledge_chunks.jsonl")


def test_mini_backend_still_works(tmp_path: Path) -> None:
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        corpus=[],
        out_dir=tmp_path,
        retriever=MiniCorpusRetriever(load_mini_corpus()),
        answer_generator=DeterministicAnswerGenerator(),
    )

    predictions = agent.run(load_mini_golds())

    assert len(predictions) == 5
    assert {prediction.answer_status for prediction in predictions} == {"answered"}
    assert predictions[-1].source_chunk_ids == ["chunk_access"]


def test_runner_uses_injected_retriever_and_generator(tmp_path: Path) -> None:
    field = load_mini_golds()[0]
    retriever = FakeRetriever()
    generator = FakeAnswerGenerator()
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        corpus=[],
        out_dir=tmp_path,
        retriever=retriever,
        answer_generator=generator,
        retrieval_backend="fake",
        generation_backend="fake",
    )

    predictions = agent.run([field])

    assert retriever.called is True
    assert generator.called is True
    assert predictions[0].answer_value == "fake answer"


def test_llm_not_called_when_no_evidence(tmp_path: Path) -> None:
    field = load_mini_golds()[0]
    generator = FakeAnswerGenerator()
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        corpus=[],
        out_dir=tmp_path,
        retriever=EmptyRetriever(),
        answer_generator=generator,
        generation_backend="llm",
    )

    predictions = agent.run([field])

    assert generator.called is False
    assert predictions[0].answer_status == "not_found"


def test_llm_not_called_for_global_only_clue(tmp_path: Path) -> None:
    field = load_mini_golds()[0]
    generator = FakeAnswerGenerator()
    agent = FieldFillingAgent(
        target_namespace="xixian_4",
        corpus=[],
        out_dir=tmp_path,
        retriever=GlobalOnlyRetriever(),
        answer_generator=generator,
        generation_backend="llm",
    )

    predictions = agent.run([field])

    assert generator.called is False
    assert predictions[0].answer_status == "partial_clue"


def test_cli_requires_qdrant_config_when_qdrant_backend(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = write_empty_service_config(tmp_path, repo_root)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "nested_doc_rag.cli",
            "run-agent",
            "--config",
            str(config_path),
            "--gold",
            "examples/mini_data/gold_fields.jsonl",
            "--target-namespace",
            "xixian_4",
            "--retrieval-backend",
            "qdrant",
            "--generation-backend",
            "deterministic",
            "--out-dir",
            str(tmp_path / "out"),
            "--no-writeback",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "retrieval-backend=qdrant requires qdrant_path, collection_name, embedding_endpoint, embedding_model" in completed.stderr


def test_cli_requires_llm_config_when_llm_backend(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = write_empty_service_config(tmp_path, repo_root)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "nested_doc_rag.cli",
            "run-agent",
            "--config",
            str(config_path),
            "--gold",
            "examples/mini_data/gold_fields.jsonl",
            "--corpus",
            "examples/mini_data/knowledge_chunks.jsonl",
            "--target-namespace",
            "xixian_4",
            "--retrieval-backend",
            "mini",
            "--generation-backend",
            "llm",
            "--out-dir",
            str(tmp_path / "out"),
            "--no-writeback",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "generation-backend=llm requires chat_endpoint and chat_model" in completed.stderr


def test_cli_mini_mode_does_not_require_real_services(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = write_empty_service_config(tmp_path, repo_root)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "nested_doc_rag.cli",
            "run-agent",
            "--config",
            str(config_path),
            "--gold",
            "examples/mini_data/gold_fields.jsonl",
            "--corpus",
            "examples/mini_data/knowledge_chunks.jsonl",
            "--target-namespace",
            "xixian_4",
            "--retrieval-backend",
            "mini",
            "--generation-backend",
            "deterministic",
            "--out-dir",
            str(tmp_path / "out"),
            "--no-writeback",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=True,
    )

    result = json.loads(completed.stdout)
    assert result["field_count"] == 5
    assert (tmp_path / "out" / "predictions.jsonl").exists()


def write_empty_service_config(tmp_path: Path, repo_root: Path) -> Path:
    config_path = tmp_path / "empty_services.yaml"
    config_path.write_text(
        f"""
paths:
  project_root: {repo_root}
services:
  embedding_endpoint: ""
  embedding_model: ""
  rerank_endpoint: ""
  rerank_model: ""
  chat_endpoint: ""
  chat_model: ""
qdrant:
  collection_name: ""
retrieval:
  target_namespace: xixian_4
agent:
  retrieval_backend: mini
  generation_backend: deterministic
  enable_rerank: false
""",
        encoding="utf-8",
    )
    return config_path


class FakeRetriever:
    last_metadata = {"qdrant_hit_count": 0}

    def __init__(self) -> None:
        self.called = False

    def retrieve(self, query_plan, field):  # noqa: ANN001
        self.called = True
        return [
            {
                "chunk_id": "fake_chunk",
                "namespace": query_plan.target_namespace,
                "source_type": "main_excel_capability",
                "field_id": field.field_id,
                "answer_value": "fake answer",
                "answer_status": "answered",
                "source_chunk_ids": ["fake_chunk"],
            }
        ]


class EmptyRetriever:
    last_metadata = {"qdrant_hit_count": 0}

    def retrieve(self, query_plan, field):  # noqa: ANN001
        return []


class GlobalOnlyRetriever:
    last_metadata = {"qdrant_hit_count": 0}

    def retrieve(self, query_plan, field):  # noqa: ANN001
        return [
            {
                "chunk_id": "global_chunk",
                "namespace": "global",
                "source_type": "intro_doc_paragraph",
                "field_id": field.field_id,
                "answer_value": "global clue",
                "answer_status": "answered",
                "source_chunk_ids": ["global_chunk"],
            }
        ]


class FakeAnswerGenerator:
    chat_model = "fake-model"

    def __init__(self) -> None:
        self.called = False

    def generate(self, field, evidence_bundle, query_plan, *, trace_context=None):  # noqa: ANN001
        self.called = True
        return FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value=evidence_bundle.selected_chunks[0]["answer_value"],
            answer_status="answered",
            confidence=0.9,
            source_chunk_ids=["fake_chunk"],
            evidence_attachment_ids=[],
            validation={"generation_backend": "fake"},
            method_name="fake_generator",
        )
