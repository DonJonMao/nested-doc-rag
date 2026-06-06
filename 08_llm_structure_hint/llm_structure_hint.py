from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_CLASSIFICATION = Path(__file__).resolve().parents[1] / "artifacts/07_agent_need_audit/table_candidate_classification.json"
DEFAULT_SEGMENTS = Path(__file__).resolve().parents[1] / "artifacts/04b_embedded_object_parse/embedded_segments.jsonl"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/08_llm_structure_hint"
DEFAULT_URL = "http://localhost:8006/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

RECOMMENDED_CATEGORIES = {
    "绩效考核-年度考核明细",
    "服务报告-IP地址段",
    "服务报告-满意度评价",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def display_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if limit and len(text) > limit:
        return text[: limit - 1] + "..."
    return text


def parse_anchor(anchor: str) -> tuple[str, str, int | None]:
    match = re.match(r"(.+)!([A-Z]+\d+)\s+table\s+(\d+)", anchor)
    if not match:
        return "", "", None
    return match.group(1), match.group(2), int(match.group(3))


def select_candidates(candidates: list[dict[str, Any]], policy: str, category: str | None = None) -> list[dict[str, Any]]:
    if policy == "recommended":
        return [item for item in candidates if item.get("table_category") in RECOMMENDED_CATEGORIES]
    if policy == "original_26":
        return [item for item in candidates if item.get("source_group") == "original_26"]
    if policy == "all":
        return candidates[:]
    if policy == "category":
        return [item for item in candidates if item.get("table_category") == category]
    raise ValueError(f"unknown policy: {policy}")


def group_table_rows(segments_path: Path) -> dict[tuple[str, str, str, int], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
    for segment in read_jsonl(segments_path):
        anchor = segment.get("local_anchor", {})
        if segment.get("segment_type") != "embedded_docx_table_row":
            continue
        table_index = anchor.get("table_index")
        if table_index is None:
            continue
        key = (
            segment.get("parent_file_name", ""),
            segment.get("parent_sheet_name", ""),
            segment.get("parent_source_cell", ""),
            int(table_index),
        )
        row = {
            "row_index": anchor.get("row_index"),
            "row_values": anchor.get("row_values") or [],
            "current_header": anchor.get("table_header") or [],
            "current_text": segment.get("raw_text") or "",
        }
        groups.setdefault(key, []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda item: item.get("row_index") or 0)
    return groups


def build_prompt(candidate: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    source = {
        "file_name": candidate.get("file_name"),
        "anchor": candidate.get("anchor"),
        "source_group": candidate.get("source_group"),
        "table_category": candidate.get("table_category"),
        "suggested_handling": candidate.get("suggested_handling"),
    }
    return (
        "你是一个表格结构判定器。请只输出 JSON，不要输出 Markdown，不要解释。\n"
        "你只读取表格格式，不能改写正文内容，不能补充原文没有的信息。\n"
        "输出 JSON 必须符合下面 schema：\n"
        "{\n"
        '  "table_type": string,\n'
        '  "context": string,\n'
        '  "title_rows": [int],\n'
        '  "context_rows": [int],\n'
        '  "header_rows": [int],\n'
        '  "data_rows": [int],\n'
        '  "group_rows": [int],\n'
        '  "column_headers": [string],\n'
        '  "row_strategy": string,\n'
        '  "confidence": number,\n'
        '  "notes": string\n'
        "}\n\n"
        "判定原则：\n"
        "1. title_rows 是标题行；context_rows 是说明/上下文行；header_rows 是真正表头行。\n"
        "2. data_rows 是应该生成知识块的数据行；group_rows 是分组标题行。\n"
        "3. column_headers 应给出代码重切时使用的列名，例如把“列2/列3”还原为真实选项列。\n"
        "4. 如果表格适合按行切分，row_strategy 写 one_row_one_segment；如果是问卷矩阵/命令矩阵/键值表，请写清楚策略。\n"
        "5. 只输出结构 hint，最终正文由代码从原始行读取。\n\n"
        f"表格来源：\n{json.dumps(source, ensure_ascii=False, indent=2)}\n\n"
        f"表格行：\n{json.dumps(rows, ensure_ascii=False, indent=2)}\n"
    )


def prompt_hash(model: str, prompt: str) -> str:
    return hashlib.sha256((model + "\n" + prompt).encode("utf-8")).hexdigest()[:16]


def call_with_curl(
    *,
    url: str,
    model: str,
    api_key: str,
    prompt: str,
    temperature: float,
    timeout: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": "你只做表格结构判定，必须输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ],
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "--max-time",
                str(timeout),
                "-X",
                "POST",
                url,
                "-H",
                "Content-Type: application/json",
                "-H",
                f"Authorization: Bearer {api_key}",
                "-d",
                f"@{tmp_path}",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-json API response: {proc.stdout[:500]}") from exc


def extract_content(response: dict[str, Any]) -> str:
    return response["choices"][0]["message"]["content"]


def parse_model_json(content: str) -> tuple[dict[str, Any] | None, str | None]:
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    if not cleaned.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", cleaned)
        cleaned = match.group(0) if match else cleaned
    try:
        return json.loads(cleaned), None
    except json.JSONDecodeError as exc:
        return None, repr(exc)


def validate_hint(hint: dict[str, Any] | None, available_rows: list[int]) -> dict[str, Any]:
    if hint is None:
        return {"status": "invalid_json", "errors": ["model output is not valid JSON"]}
    errors: list[str] = []
    required = ["table_type", "header_rows", "data_rows", "column_headers", "row_strategy", "confidence"]
    for key in required:
        if key not in hint:
            errors.append(f"missing {key}")
    for key in ["title_rows", "context_rows", "header_rows", "data_rows", "group_rows"]:
        value = hint.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
            errors.append(f"{key} must be a list of integers")
            continue
        unknown = sorted(set(value) - set(available_rows))
        if unknown:
            errors.append(f"{key} contains unknown rows: {unknown}")
    if not isinstance(hint.get("column_headers", []), list):
        errors.append("column_headers must be a list")
    try:
        confidence = float(hint.get("confidence"))
        if not 0 <= confidence <= 1:
            errors.append("confidence must be between 0 and 1")
    except (TypeError, ValueError):
        errors.append("confidence must be numeric")
    return {"status": "valid" if not errors else "invalid", "errors": errors}


def run(
    classification_path: Path,
    segments_path: Path,
    out_dir: Path,
    policy: str,
    category: str | None,
    limit: int | None,
    url: str,
    model: str,
    api_key_env: str,
    temperature: float,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    candidates = read_json(classification_path)
    selected = select_candidates(candidates, policy, category)
    if limit is not None:
        selected = selected[:limit]

    table_groups = group_table_rows(segments_path)
    api_key = os.environ.get(api_key_env)
    if not dry_run and not api_key:
        raise RuntimeError(f"missing API key env var: {api_key_env}")

    records: list[dict[str, Any]] = []
    for candidate in selected:
        sheet_name, source_cell, table_index = parse_anchor(candidate.get("anchor", ""))
        key = (candidate.get("file_name", ""), sheet_name, source_cell, int(table_index or -1))
        rows = table_groups.get(key, [])
        prompt = build_prompt(candidate, rows)
        cache_key = prompt_hash(model, prompt)
        cache_path = cache_dir / f"{cache_key}.json"
        response: dict[str, Any] | None = None
        status = "dry_run" if dry_run else "called"
        error = None

        if cache_path.exists():
            response = read_json(cache_path)
            status = "cache_hit"
        elif not dry_run:
            try:
                response = call_with_curl(
                    url=url,
                    model=model,
                    api_key=api_key or "",
                    prompt=prompt,
                    temperature=temperature,
                    timeout=timeout,
                )
                write_json(cache_path, response)
            except Exception as exc:
                error = repr(exc)
                status = "error"

        content = extract_content(response) if response else ""
        hint, parse_error = parse_model_json(content) if content else (None, None)
        validation = validate_hint(hint, [row["row_index"] for row in rows]) if content else {"status": status, "errors": []}
        if parse_error:
            validation["errors"].append(parse_error)

        records.append(
            {
                "hint_id": cache_key,
                "call_status": status,
                "model": model,
                "source_group": candidate.get("source_group"),
                "table_category": candidate.get("table_category"),
                "file_name": candidate.get("file_name"),
                "anchor": candidate.get("anchor"),
                "table_no": table_index,
                "row_count": len(rows),
                "table_rows": rows,
                "llm_hint": hint,
                "raw_response": content,
                "validation": validation,
                "error": error,
            }
        )

    summary = {
        "policy": policy,
        "model": model,
        "curl_backend": True,
        "requested_tables": len(records),
        "call_status_counts": dict(Counter(item["call_status"] for item in records)),
        "validation_status_counts": dict(Counter(item["validation"]["status"] for item in records)),
        "table_category_counts": dict(Counter(item["table_category"] for item in records)),
    }
    write_jsonl(out_dir / "table_structure_hints.jsonl", records)
    write_json(out_dir / "summary.json", summary)
    write_visualization(out_dir / "visualization.md", summary, records)
    return summary


def write_visualization(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# Step 08 LLM 表格结构 Hint\n")
    lines.append("本步骤通过 curl 调用 DeepSeek，只生成表格结构 JSON hint；正文仍由规则代码从原文读取。\n")
    lines.append("## 总览\n")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    for key, value in summary.items():
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"| `{key}` | `{value}` |")
    lines.append("\n## Hint 明细\n")
    lines.append("| status | validation | file | anchor | category | table_type | headers | strategy |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for item in records:
        hint = item.get("llm_hint") or {}
        headers = ", ".join(str(value) for value in hint.get("column_headers", []))
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{item['call_status']}`",
                    f"`{item['validation']['status']}`",
                    f"`{display_text(item['file_name'], 40)}`",
                    f"`{display_text(item['anchor'], 40)}`",
                    display_text(item["table_category"], 40),
                    display_text(hint.get("table_type"), 40),
                    display_text(headers, 80),
                    display_text(hint.get("row_strategy"), 80),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate LLM table structure hints via curl-compatible chat completions API.")
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--segments", type=Path, default=DEFAULT_SEGMENTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--policy", choices=["recommended", "original_26", "all", "category"], default="recommended")
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--url", default=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_URL))
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = run(
        args.classification,
        args.segments,
        args.out_dir,
        args.policy,
        args.category,
        args.limit,
        args.url,
        args.model,
        args.api_key_env,
        args.temperature,
        args.timeout,
        args.dry_run,
    )
    print(f"generated {summary['requested_tables']} LLM structure hints -> {args.out_dir}")


if __name__ == "__main__":
    main()
