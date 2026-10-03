#!/usr/bin/env python3
"""Explicit HTTP protocol substitute for Docker plumbing, never model quality.

Reads only the live request, never datasets, gold, templates or heldout answers.
It handles exact label/value matches and otherwise abstains. Every response is
marked as a stub and usage remains unknown rather than invented as zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def vector(text: str) -> list[float]:
    values = [0.0] * 64
    for char in text.casefold():
        if char.isalnum():
            index = int.from_bytes(hashlib.sha256(char.encode()).digest()[:2], "big") % len(values)
            values[index] += 1
    norm = math.sqrt(sum(v * v for v in values)) or 1
    return [v / norm for v in values]


def json_after(text: str, marker: str) -> Any:
    return json.JSONDecoder().raw_decode(text.split(marker, 1)[1].lstrip())[0]


def exact_hit(question: str, hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    for hit in hits:
        name = str(hit.get("field_name") or "").strip()
        value = hit.get("field_value")
        # Older stage prompts expose native row text but omit typed fields.
        # Apply the same two-column protocol rule rather than privileging A2.
        native = str(hit.get("raw_source_text") or hit.get("raw_text") or "")
        parts = native.split(" / ")
        if not name and len(parts) == 2:
            name, value = parts
        kind = hit.get("evidence_kind")
        if name and name in question and value not in (None, "") and kind in (None, "structured_field"):
            return {**hit, "field_value": value}
    return None


def chat_reply(request: dict[str, Any]) -> dict[str, Any]:
    messages = request.get("messages", [])
    system = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    user = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
    if "Evidence Sufficiency Check" in system:
        payload = json_after(user, "\n")
        question = str(payload["form_item"].get("question_text") or "")
        hit = exact_hit(question, payload.get("retrieved_evidence", []))
        return {
            "sufficient": hit is not None,
            "missing_facts": [] if hit else [question or "可直接支持当前字段的事实"],
            "supporting_evidence_ids": [hit["evidence_id"]] if hit else [],
            "reason": "Explicit protocol stub: exact label/value only; not a semantic sufficiency evaluation",
        }
    item = json_after(user, "form_item_without_heldout_answer:\n")
    hits = json_after(user, "retrieved_chunks:\n")
    hit = exact_hit(str(item.get("question_text") or ""), hits)
    if hit:
        identity = str(hit.get("evidence_id") or hit["chunk_id"])
        return {
            "answer_status": "answered", "answer_value": str(hit["field_value"]), "confidence": 1.0,
            "source_chunk_ids": [identity], "evidence_attachment_ids": [],
            "reference_source_documents": [{"chunk_id": identity, "quote": hit.get("raw_source_text") or hit.get("raw_text") or hit.get("text") or "", "reason": "Explicit protocol stub exact label/value"}],
        }
    return {
        "answer_status": "not_found", "answer_value": "未找到", "confidence": 0.0,
        "source_chunk_ids": [], "evidence_attachment_ids": [], "reference_source_documents": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = json.loads(raw)
            error = None
            try:
                if self.path == "/v1/embeddings":
                    texts = request["input"]
                    texts = [texts] if isinstance(texts, str) else texts
                    response = {"data": [{"index": i, "embedding": vector(text)} for i, text in enumerate(texts)]}
                elif self.path == "/rerank":
                    query = Counter(c for c in str(request["query"]) if c.isalnum())
                    documents = request["documents"]
                    scores = [(i, sum((query & Counter(str(doc))).values())) for i, doc in enumerate(documents)]
                    scores.sort(key=lambda pair: (-pair[1], pair[0]))
                    response = {"results": [{"index": i, "relevance_score": float(score), "document": {"text": documents[i]}} for i, score in scores[:int(request["top_n"])]]}
                elif self.path == "/v1/chat/completions":
                    response = {"choices": [{"message": {"content": json.dumps(chat_reply(request), ensure_ascii=False)}}]}
                else:
                    self.send_error(404)
                    return
                response["model"] = "vnext-explicit-protocol-stub"
                response["usage"] = None
                status = 200
            except Exception as exception:
                error = type(exception).__name__
                response = {"error": {"type": error, "message": "Unsupported explicit stub request"}}
                status = 400
            body = json.dumps(response, ensure_ascii=False).encode()
            with lock, args.ledger.open("a") as stream:
                stream.write(json.dumps({"models_kind": "stub", "path": self.path, "request_sha256": hashlib.sha256(raw).hexdigest(), "response_sha256": hashlib.sha256(body).hexdigest(), "http_status": status, "error_type": error, "usage": "unknown"}) + "\n")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
