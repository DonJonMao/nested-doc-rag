from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nested_doc_rag.config import app_config_from_dict, load_app_config


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
  plan: layered
  vector_top_k: 11
  query_layers: [fact, evidence]
grounding:
  min_strength_for_answered: E4
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
    assert config.retrieval.plan == "layered"
    assert config.retrieval.sufficiency_enabled is True
    assert config.retrieval.expand_parent_payload is False
    assert config.retrieval.rerank_top_n == 6
    assert config.retrieval.query_layers == ["fact", "evidence"]
    assert config.grounding.field_binding_enabled is True
    assert config.grounding.relaxed_writeback_gate_enabled is False
    assert config.grounding.min_strength_for_answered == "E4"
    assert config.writeback.allow_uncertain is False
    assert config.writeback.uncertain_comment_prefix == "[UNCERTAIN]"


def test_env_alias_and_path_resolution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_ENDPOINT", "http://alias/embeddings")
    monkeypatch.setenv("QDRANT_COLLECTION", "alias_collection")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_API_KEY_ENV", "LOCAL_QDRANT_API_KEY")
    monkeypatch.setenv("TARGET_NAMESPACE", "xixian_6")

    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")

    assert config.services.embedding_endpoint == "http://alias/embeddings"
    assert config.qdrant.collection_name == "alias_collection"
    assert config.qdrant.url == "http://localhost:6333"
    assert config.qdrant.api_key_env == "LOCAL_QDRANT_API_KEY"
    assert config.retrieval.target_namespace == "xixian_6"
    assert config.paths.artifacts_dir == (tmp_path / "artifacts").resolve()
    assert config.paths.qdrant_path == (tmp_path / "artifacts/15_vector_store/qdrant").resolve()


def test_agentscope_config_defaults_and_yaml_off_normalization(tmp_path: Path) -> None:
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")
    assert config.agentscope.enabled is False
    assert config.agentscope.mode == "off"
    assert config.retrieval.sufficiency_enabled is True
    assert config.agentic_mas.enabled is False
    assert config.agentic_mas.prompt_version == "agentic_v1"

    config = app_config_from_dict(
        {
            "paths": {"project_root": str(tmp_path)},
            "agentscope": {"enabled": False, "mode": False},
        },
        project_root_base=tmp_path,
    )
    assert config.agentscope.mode == "off"

    config = app_config_from_dict(
        {
            "paths": {"project_root": str(tmp_path)},
            "agentscope": {"enabled": True, "mode": "equivalent_mas"},
        },
        project_root_base=tmp_path,
    )
    assert config.agentscope.enabled is True
    assert config.agentscope.mode == "equivalent_mas"

    config = app_config_from_dict(
        {
            "paths": {"project_root": str(tmp_path)},
            "agentscope": {"enabled": True, "mode": "agentic_mas"},
            "agentic_mas": {"enabled": True, "max_rounds": 4},
        },
        project_root_base=tmp_path,
    )
    assert config.agentscope.mode == "agentic_mas"
    assert config.agentic_mas.enabled is True
    assert config.agentic_mas.max_rounds == 4

    with pytest.raises(ValueError, match="agentscope.mode"):
        app_config_from_dict(
            {
                "paths": {"project_root": str(tmp_path)},
                "agentscope": {"enabled": True, "mode": "new_behavior"},
            },
            project_root_base=tmp_path,
        )


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
    assert value["services"]["embedding_endpoint"] == "http://111.19.156.74:8001/v1/embeddings"
    assert value["services"]["rerank_endpoint"] == "http://111.19.156.74:8002/rerank"
    assert value["services"]["chat_endpoint"] == "http://111.19.156.30:8006/v1/chat/completions"
    assert value["retrieval"]["plan"] == "layered"
    assert value["retrieval"]["sufficiency_enabled"] is True
    assert value["agentscope"] == {"enabled": False, "mode": "off"}
    assert value["agentic_mas"]["enabled"] is False
    assert value["retrieval"]["expand_parent_payload"] is False
    assert value["grounding"]["field_binding_enabled"] is True
    assert value["grounding"]["relaxed_writeback_gate_enabled"] is False
    assert "mode" not in value["retrieval"]
    assert "retrieval_mode" not in value["retrieval"]
    assert "retrieval_plan" not in value["retrieval"]
    assert value["qdrant"]["collection_name"] == "datacenter_chunks_v1"
    assert value["qdrant"]["url"] == ""
    assert value["writeback"]["allow_uncertain"] is False
    assert value["writeback"]["embed_evidence_images"] is True
    assert value["writeback"]["evidence_image_mode"] == "append_sheet"
    assert value["writeback"]["max_comment_chars"] == 2000


@pytest.mark.parametrize("filename", ["default.yaml", "docker.yaml", "local.example.yaml"])
def test_production_configs_use_sufficiency_without_default_mas_or_historical_baseline(filename: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_app_config(f"config/{filename}", project_root=repo_root, env={})

    assert config.retrieval.sufficiency_enabled is True
    assert config.agentscope.enabled is False
    assert config.agentscope.mode == "off"
    assert config.agentic_mas.enabled is False
    assert not hasattr(config.agentic_mas, "monotonic_writeback37")
    assert not hasattr(config.agentic_mas, "baseline_writeback_run_dir")
    assert not hasattr(config.agentic_mas, "preserve_writeback_allowed")
    assert not hasattr(config.agentic_mas, "promotion_only")


def test_sufficiency_switch_obeys_yaml_env_and_cli_priority(tmp_path: Path) -> None:
    path = tmp_path / "runtime.yaml"
    path.write_text("retrieval:\n  sufficiency_enabled: false\n", encoding="utf-8")
    options = {"project_root": tmp_path, "default_config": tmp_path / "missing.yaml"}
    config = load_app_config(path, env={}, **options)
    assert config.retrieval.sufficiency_enabled is False
    config = load_app_config(path, env={"NESTED_DOC_RAG__RETRIEVAL__SUFFICIENCY_ENABLED": "true"}, **options)
    assert config.retrieval.sufficiency_enabled is True
    config = load_app_config(
        path, env={"NESTED_DOC_RAG__RETRIEVAL__SUFFICIENCY_ENABLED": "true"},
        cli_overrides={"retrieval": {"sufficiency_enabled": False}}, **options,
    )
    assert config.retrieval.sufficiency_enabled is False


def test_legacy_retrieval_config_normalizes_to_layered(tmp_path: Path) -> None:
    with pytest.warns(DeprecationWarning):
        config = app_config_from_dict(
            {
                "paths": {"project_root": str(tmp_path)},
                "retrieval": {"mode": "dense", "retrieval_mode": "flat", "retrieval_plan": "flat"},
            },
            project_root_base=tmp_path,
        )

    assert config.retrieval.plan == "layered"


def test_unsupported_retrieval_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="hybrid"):
        app_config_from_dict(
            {
                "paths": {"project_root": str(tmp_path)},
                "retrieval": {"mode": "hybrid"},
            },
            project_root_base=tmp_path,
        )

    with pytest.raises(ValueError, match="hybrid"):
        app_config_from_dict(
            {
                "paths": {"project_root": str(tmp_path)},
                "retrieval": {"plan": "hybrid"},
            },
            project_root_base=tmp_path,
        )
