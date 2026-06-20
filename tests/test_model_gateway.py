from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nested_doc_rag import model_gateway
from nested_doc_rag.agent.backends import LLMAnswerGenerator
from nested_doc_rag.agent.policies import build_query_plan
from nested_doc_rag.agent.state import EvidenceBundle
from nested_doc_rag.agent.step15_runner import Step15AgentRunner
from nested_doc_rag.config import load_app_config
from nested_doc_rag.embedding import EmbeddingClient, RerankClient
from nested_doc_rag.schemas.eval import FieldGold


class FakeHttp:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post_json(self, url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        return self.response


def test_gateway_disabled_preserves_existing_chat_url(monkeypatch) -> None:
    clear_gateway_env(monkeypatch)
    http = FakeHttp(chat_response("西咸4号楼301机房"))
    generator = LLMAnswerGenerator(chat_endpoint="http://chat", chat_model="demo-model", api_key="secret", http_client=http)

    generator.generate(make_field(), make_bundle(), build_query_plan(make_field(), target_namespace="xixian_4"))

    assert http.calls[0]["url"] == "http://chat"
    assert http.calls[0]["headers"] == {"Authorization": "Bearer secret"}


def test_gateway_enabled_overrides_chat_url(monkeypatch) -> None:
    set_gateway_env(monkeypatch)
    http = FakeHttp(chat_response("西咸4号楼301机房"))
    generator = LLMAnswerGenerator(chat_endpoint="http://chat", chat_model="demo-model", api_key="upstream-secret", http_client=http)

    generator.generate(make_field(), make_bundle(), build_query_plan(make_field(), target_namespace="xixian_4"))

    assert http.calls[0]["url"] == "http://gateway/internal/model-gateway/v1/chat/completions"


def test_gateway_enabled_overrides_embedding_url(monkeypatch) -> None:
    set_gateway_env(monkeypatch)
    http = FakeHttp({"data": [{"index": 0, "embedding": [1.0, 2.0]}]})
    client = EmbeddingClient(endpoint="http://embedding", model="embedding-model")
    client.http = http

    assert client.embed(["hello"]) == [[1.0, 2.0]]
    assert http.calls[0]["url"] == "http://gateway/internal/model-gateway/v1/embeddings"


def test_gateway_enabled_overrides_rerank_url(monkeypatch) -> None:
    set_gateway_env(monkeypatch)
    http = FakeHttp({"results": [{"index": 0, "relevance_score": 0.9}]})
    client = RerankClient(endpoint="http://rerank", model="rerank-model")
    client.http = http

    assert client.rerank("query", ["doc"], top_n=1) == [{"index": 0, "relevance_score": 0.9}]
    assert http.calls[0]["url"] == "http://gateway/internal/model-gateway/v1/rerank"


def test_chat_caller_sends_gateway_headers(monkeypatch) -> None:
    set_gateway_env(monkeypatch)
    http = FakeHttp(chat_response("西咸4号楼301机房"))
    generator = LLMAnswerGenerator(chat_endpoint="http://chat", chat_model="demo-model", api_key="upstream-secret", http_client=http)

    generator.generate(make_field(), make_bundle(), build_query_plan(make_field(), target_namespace="xixian_4"))

    headers = http.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer gateway-token"
    assert headers["X-NDR-Run-ID"] == "run-123"
    assert headers["X-NDR-Job-ID"] == "job-123"
    assert headers["X-NDR-User-ID"] == "user-123"
    assert headers["X-NDR-Workspace-ID"] == "workspace-123"
    assert headers["X-NDR-Model-Kind"] == "chat"
    assert headers["X-NDR-Model-Purpose"] == "step15_answer"
    assert headers["X-NDR-Field-ID"] == "field_room_name"
    assert headers["X-NDR-Request-ID"]


def test_embedding_client_sends_gateway_headers(monkeypatch) -> None:
    set_gateway_env(monkeypatch)
    http = FakeHttp({"data": [{"index": 0, "embedding": [1.0]}]})
    client = EmbeddingClient(endpoint="http://embedding", model="embedding-model")
    client.http = http

    client.embed_query("query")

    headers = http.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer gateway-token"
    assert headers["X-NDR-Model-Kind"] == "embedding"
    assert headers["X-NDR-Model-Purpose"] == "query_embedding"


def test_rerank_client_sends_gateway_headers(monkeypatch) -> None:
    set_gateway_env(monkeypatch)
    http = FakeHttp({"results": [{"index": 0, "relevance_score": 0.9}]})
    client = RerankClient(endpoint="http://rerank", model="rerank-model")
    client.http = http

    client.rerank("query", ["doc"], top_n=1)

    headers = http.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer gateway-token"
    assert headers["X-NDR-Model-Kind"] == "rerank"
    assert headers["X-NDR-Model-Purpose"] == "rerank"


def test_step15_passes_field_id_to_chat_gateway_headers(monkeypatch, tmp_path: Path) -> None:
    set_gateway_env(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_call_deepseek_json(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"answer_status": "not_found", "answer_value": "", "source_chunk_ids": []}

    monkeypatch.setattr("nested_doc_rag.agent.step15_runner.call_deepseek_json", fake_call_deepseek_json)
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml")
    runner = Step15AgentRunner(
        config=config,
        target_namespace="xixian_4",
        out_dir=tmp_path,
        retrieval_fn=lambda query: None,
        chat_retry_backoff_seconds=0,
    )

    runner.call_chat_with_retries(call_kind="answer", field_id="field_42", caller=None, kwargs={"messages": []})

    assert captured["url"] == "http://gateway/internal/model-gateway/v1/chat/completions"
    assert captured["headers"]["X-NDR-Field-ID"] == "field_42"
    assert captured["headers"]["X-NDR-Model-Purpose"] == "step15_answer"


def test_gateway_error_keeps_request_id() -> None:
    assert model_gateway.gateway_error_request_id({"error": {"request_id": "req-1"}}) == "req-1"
    assert model_gateway.gateway_error_request_id({"request_id": "req-2"}) == "req-2"


def test_gateway_token_not_logged(monkeypatch, capsys) -> None:
    set_gateway_env(monkeypatch)

    headers = model_gateway.headers_for("chat", "step15_answer", field_id="field_1")
    captured = capsys.readouterr()

    assert headers["Authorization"] == "Bearer gateway-token"
    assert "gateway-token" not in captured.out
    assert "gateway-token" not in captured.err


def set_gateway_env(monkeypatch) -> None:
    monkeypatch.setenv("NDR_MODEL_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("NDR_MODEL_GATEWAY_BASE_URL", "http://gateway/internal/model-gateway")
    monkeypatch.setenv("NDR_MODEL_GATEWAY_TOKEN", "gateway-token")
    monkeypatch.setenv("NDR_RUN_ID", "run-123")
    monkeypatch.setenv("NDR_JOB_ID", "job-123")
    monkeypatch.setenv("NDR_USER_ID", "user-123")
    monkeypatch.setenv("NDR_WORKSPACE_ID", "workspace-123")


def clear_gateway_env(monkeypatch) -> None:
    for name in [
        "NDR_MODEL_GATEWAY_ENABLED",
        "NDR_MODEL_GATEWAY_BASE_URL",
        "NDR_MODEL_GATEWAY_TOKEN",
        "NDR_RUN_ID",
        "NDR_JOB_ID",
        "NDR_USER_ID",
        "NDR_WORKSPACE_ID",
    ]:
        monkeypatch.delenv(name, raising=False)


def make_field() -> FieldGold:
    return FieldGold.from_dict(
        {
            "field_id": "field_room_name",
            "row_index": 4,
            "target_cell": "C4",
            "question_text": "机房名称是什么",
            "expected_value": "SHOULD_NOT_LEAK",
            "field_type": "text",
            "required": True,
            "must_have_evidence": True,
        }
    )


def make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        field_id="field_room_name",
        selected_chunks=[
            {
                "chunk_id": "chunk_room",
                "namespace": "xixian_4",
                "source_type": "main_excel_capability",
                "corpus_layer": "fact",
                "raw_text": "机房名称：西咸4号楼301机房。",
                "source": {"file_name": "demo.xlsx", "sheet": "Sheet1", "row": 4},
            }
        ],
        reference_chunks=[],
        ignored_chunks=[],
        decision="use_direct_evidence",
        reason="direct target evidence",
    )


def chat_response(answer: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "answer_value": answer,
                            "answer_status": "answered",
                            "confidence": 0.95,
                            "source_chunk_ids": ["chunk_room"],
                            "reason": "selected evidence states the room name",
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }
