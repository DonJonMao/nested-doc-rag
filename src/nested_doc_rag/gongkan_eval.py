from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .config import load_app_config
from .io import display_text, read_jsonl
from .llm import extract_json_object

BASE_CLOUD_FILE = "基地云机房信息调研表.xlsx"


def select_eval_items(
    rows: list[int] | None,
    *,
    form_items_path: Path | None = None,
    base_cloud_file: str | None = None,
) -> list[dict[str, Any]]:
    if form_items_path is None:
        form_items_path = load_app_config().paths.artifacts_dir / "12_gongkan_form_analysis" / "form_items.jsonl"
        base_cloud_file = base_cloud_file or BASE_CLOUD_FILE
    if not form_items_path.is_file():
        raise RuntimeError(f"form items file does not exist: {form_items_path}")
    items = read_jsonl(form_items_path)
    if base_cloud_file is not None:
        items = [item for item in items if item.get("file_name") == base_cloud_file]
    return select_form_items(items, rows)


def select_form_items(items: list[dict[str, Any]], rows: list[int] | None) -> list[dict[str, Any]]:
    """Keep every field identity, including equal row numbers on different sheets."""
    try:
        available_rows = {int(item["row_index"]) for item in items}
    except (TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("each form item requires a positive row_index") from exc
    if any(row < 1 for row in available_rows):
        raise RuntimeError("each form item requires a positive row_index")
    missing = [row for row in rows or [] if row not in available_rows]
    if missing:
        raise RuntimeError(f"missing form rows: {missing}")
    selected = items if rows is None else [item for row in dict.fromkeys(rows) for item in items if int(item["row_index"]) == row]
    if not selected:
        raise RuntimeError("no form fields selected")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        copied = dict(item)
        if not copied.get("form_item_id"):
            identity = [copied.get(key) for key in ("file_name", "sheet_name", "row_index", "target_cell", "question_text")]
            copied["form_item_id"] = "form_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        field_id = str(copied["form_item_id"])
        if field_id in seen:
            raise RuntimeError(f"duplicate form field identity: {field_id}")
        seen.add(field_id)
        output.append(copied)
    return output


def build_masked_query(item: dict[str, Any], target_namespace: str) -> str:
    parts = [
        f"目标机房：{target_namespace}",
        f"任务：为表单“{item.get('file_name') or '当前表单'}”的“{item.get('target_column_label') or '待填字段'}”生成候选答案",
        f"类别：{' / '.join(item.get('category_path') or [])}",
        f"指标名称：{item.get('question_text')}",
    ]
    if item.get("instruction_text"):
        parts.append(f"填写说明及标准：{item['instruction_text']}")
    if item.get("answer_example"):
        parts.append(f"机房信息示例仅作格式参考，不是答案：{item['answer_example']}")
    if item.get("needs_evidence"):
        parts.append("该项需要证明材料或截图佐证；如命中附件，只返回附件标记，不做 OCR")
    parts.append("只能使用知识库检索结果；找不到就返回未找到")
    return "。".join(display_text(part).rstrip("。") for part in parts if display_text(part)) + "。"


def call_deepseek_json(
    *,
    url: str,
    model: str,
    api_key: str,
    messages: list[dict[str, str]],
    timeout: int,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = {"model": model, "temperature": 0, "messages": messages}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    try:
        command = [
            "curl",
            "--noproxy",
            "*",
            "-sS",
            "-X",
            "POST",
            url,
            "-H",
            "Content-Type: application/json",
            *[
                item
                for name, value in (headers if headers is not None else {"Authorization": f"Bearer {api_key}"}).items()
                for item in ("-H", f"{name}: {value}")
            ],
            "-d",
            f"@{tmp_path}",
        ]
        if timeout and timeout > 0:
            command[4:4] = ["--max-time", str(timeout)]
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip() or proc.stdout.strip()}")
    response = json.loads(proc.stdout)
    try:
        content = response["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        preview = display_text(json.dumps(response, ensure_ascii=False), 500)
        raise RuntimeError(f"chat response missing choices: {preview}") from exc
    return extract_json_object(content)


def build_judge_messages(item: dict[str, Any], generated: dict[str, Any], heldout_answer: str) -> list[dict[str, str]]:
    schema = {
        "label": "exact | acceptable | partial | mismatch | not_found_expected",
        "score": "0-1",
        "reason": "简短中文说明",
    }
    content = (
        "你是 RAG 评估器。比较 generated_answer 与 heldout_answer 是否语义一致。"
        "允许单位、空格、大小写、顺序的轻微差异；如果答案覆盖了核心事实但缺少细节，判 partial。"
        "如果 heldout_answer 本身是“无法提供/不涉及/否/是”等，也按语义判断。\n\n"
        f"question: {item.get('question_text')}\n"
        f"instruction: {item.get('instruction_text')}\n"
        f"generated_answer: {json.dumps(generated, ensure_ascii=False)}\n"
        f"heldout_answer: {heldout_answer}\n\n"
        f"只输出 JSON：{json.dumps(schema, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": "你只做答案一致性评估，必须输出 JSON。"},
        {"role": "user", "content": content},
    ]
