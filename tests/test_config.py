from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from nested_doc_rag.config import load_app_config


def test_config_priority_cli_env_yaml_code_defaults(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    default_yaml = cfg_dir / "default.yaml"
    local_yaml = cfg_dir / "local.yaml"
    default_yaml.write_text(
        """
paths:
  project_root: ..
  data_dir: default_data
services:
  embedding_endpoint: http://yaml-default/embeddings
retrieval:
  vector_top_k: 11
  query_layers: [fact, evidence]
""",
        encoding="utf-8",
    )
    local_yaml.write_text(
        """
paths:
  data_dir: local_data
services:
  embedding_endpoint: http://yaml-local/embeddings
retrieval:
  rerank_top_n: 6
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NESTED_DOC_RAG__SERVICES__EMBEDDING_ENDPOINT", "http://env/embeddings")
    monkeypatch.setenv("NESTED_DOC_RAG__RETRIEVAL__VECTOR_TOP_K", "22")

    config = load_app_config(
        local_yaml,
        default_config=default_yaml,
        project_root=tmp_path,
        cli_overrides={
            "services": {"embedding_endpoint": "http://cli/embeddings"},
            "retrieval": {"vector_top_k": 33},
        },
    )

    assert config.paths.project_root == tmp_path.resolve()
    assert config.paths.data_dir == (tmp_path / "local_data").resolve()
    assert config.services.embedding_endpoint == "http://cli/embeddings"
    assert config.retrieval.vector_top_k == 33
    assert config.retrieval.rerank_top_n == 6
    assert config.retrieval.query_layers == ["fact", "evidence"]


def test_env_alias_and_path_resolution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_ENDPOINT", "http://alias/embeddings")
    monkeypatch.setenv("QDRANT_COLLECTION", "alias_collection")
    monkeypatch.setenv("TARGET_NAMESPACE", "xixian_6")

    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")

    assert config.services.embedding_endpoint == "http://alias/embeddings"
    assert config.qdrant.collection_name == "alias_collection"
    assert config.retrieval.target_namespace == "xixian_6"
    assert config.paths.artifacts_dir == (tmp_path / "artifacts").resolve()
    assert config.paths.qdrant_path == (tmp_path / "artifacts/15_vector_store/qdrant").resolve()


def test_show_config_command_outputs_merged_config() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-m", "nested_doc_rag.cli", "show-config", "--config", "config/local.example.yaml"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=True,
    )
    value = json.loads(completed.stdout)
    assert value["paths"]["project_root"] == str(repo_root)
    assert value["services"]["embedding_endpoint"].startswith("http://localhost:")
    assert value["retrieval"]["retrieval_mode"] == "layered"
    assert value["qdrant"]["collection_name"] == "datacenter_chunks_v1"
