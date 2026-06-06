from __future__ import annotations

import json
import subprocess
from typing import Any


DEFAULT_EMBEDDING_ENDPOINT = "http://localhost:8001/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "qwen3-embedding-8b"
DEFAULT_RERANK_ENDPOINT = "http://localhost:8002/rerank"
DEFAULT_RERANK_MODEL = ""
QUERY_INSTRUCTION = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "


class CurlJsonClient:
    def __init__(self, timeout_seconds: int = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        command = [
            "curl",
            "--noproxy",
            "*",
            "--silent",
            "--show-error",
            "--max-time",
            str(self.timeout_seconds),
            "-X",
            "POST",
            url,
            "-H",
            "Content-Type: application/json",
            "-d",
            "@-",
        ]
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"curl failed with exit code {completed.returncode}: {stderr}")
        stdout = completed.stdout.decode("utf-8", errors="replace")
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"curl returned non-JSON response: {stdout[:500]}") from exc
        if isinstance(value, dict) and value.get("error"):
            raise RuntimeError(f"remote service error: {value['error']}")
        return value


class EmbeddingClient:
    def __init__(
        self,
        endpoint: str = DEFAULT_EMBEDDING_ENDPOINT,
        model: str = DEFAULT_EMBEDDING_MODEL,
        timeout_seconds: int = 180,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.http = CurlJsonClient(timeout_seconds=timeout_seconds)

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": texts}
        response = self.http.post_json(self.endpoint, payload)
        data = response.get("data")
        if not isinstance(data, list):
            raise RuntimeError(f"embedding response missing data list: {response}")
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        embeddings: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise RuntimeError(f"bad embedding item: {item}")
            embeddings.append([float(value) for value in vector])
        if len(embeddings) != len(texts):
            raise RuntimeError(f"embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
        return embeddings

    def embed_query(self, query: str) -> list[float]:
        return self.embed([QUERY_INSTRUCTION + query])[0]


class RerankClient:
    def __init__(
        self,
        endpoint: str = DEFAULT_RERANK_ENDPOINT,
        model: str = DEFAULT_RERANK_MODEL,
        timeout_seconds: int = 120,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.http = CurlJsonClient(timeout_seconds=timeout_seconds)

    def rerank(self, query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
        if not documents:
            return []
        payload: dict[str, Any] = {
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
            "return_documents": True,
        }
        if self.model:
            payload["model"] = self.model
        response = self.http.post_json(self.endpoint, payload)
        results = response.get("results")
        if not isinstance(results, list):
            raise RuntimeError(f"rerank response missing results list: {response}")
        return results
