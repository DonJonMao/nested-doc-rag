#!/usr/bin/env python3
"""Small real-service probes; never print credentials or full model responses."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nested_doc_rag.config import load_app_config, load_dotenv_file


def probe(kind: str, url: str, payload: dict[str, Any], key: str, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    body = json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    result: dict[str, Any] = {
        "kind": kind, "endpoint": url, "requested_model": payload.get("model"),
        "request_sha256": hashlib.sha256(body).hexdigest(), "models_kind": "real",
    }
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
            parsed = json.loads(raw)
            result.update({
                "http_status": response.status, "response_sha256": hashlib.sha256(raw).hexdigest(),
                "response_id": parsed.get("id"), "response_model": parsed.get("model"),
                "usage": parsed.get("usage", "unknown"),
            })
            if kind == "embedding":
                data = parsed.get("data", [])
                result["vector_dimension"] = len(data[0].get("embedding", [])) if data else 0
                valid = len(data) == len(payload["input"]) and result["vector_dimension"] > 0
            elif kind == "rerank":
                result["result_count"] = len(parsed.get("results", []))
                valid = result["result_count"] > 0
            else:
                choices = parsed.get("choices", [])
                content = choices[0].get("message", {}).get("content") if choices else None
                valid = isinstance(content, str) and bool(content.strip())
                result["content_sha256"] = hashlib.sha256((content or "").encode()).hexdigest()
            result["passed"] = valid and not parsed.get("error")
    except urllib.error.HTTPError as error:
        result.update(passed=False, http_status=error.code, error_type="HTTPError")
    except Exception as error:
        result.update(passed=False, error_type=type(error).__name__)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/docker.yaml"))
    parser.add_argument("--models-env", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    if args.models_env:
        load_dotenv_file(args.models_env)
    config = load_app_config(args.config.resolve(), env=os.environ)
    services = config.services
    jobs = [
        ("embedding", services.embedding_endpoint, {"model": services.embedding_model, "input": ["市电路数为2路。"]}, ""),
        ("rerank", services.rerank_endpoint, {"query": "市电路数", "documents": ["市电路数为2路。", "楼层数量为5层。"], "top_n": 1, "return_documents": True, **({"model": services.rerank_model} if services.rerank_model else {})}, ""),
        ("chat", services.chat_endpoint, {"model": services.chat_model, "messages": [{"role": "user", "content": "只回复OK"}], "temperature": 0, "max_tokens": 64}, os.environ.get(services.chat_api_key_env, "")),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda job: probe(*job, args.timeout), jobs))
    report = {
        "schema_version": "vnext-model-preflight-v1", "timestamp": datetime.now(UTC).isoformat(),
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "credentials_available": {services.chat_api_key_env: bool(os.environ.get(services.chat_api_key_env))},
        "models_kind": "real", "probes": results, "passed": all(item["passed"] for item in results),
        "cost": "unknown; service pricing is not declared", "service_weight_fingerprints": "unknown",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "probes": [{"kind": r["kind"], "passed": r["passed"], "elapsed_seconds": r["elapsed_seconds"], "error_type": r.get("error_type")} for r in results]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
