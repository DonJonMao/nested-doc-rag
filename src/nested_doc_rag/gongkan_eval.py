from __future__ import annotations

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
    rows: list[int],
    *,
    form_items_path: Path | None = None,
    base_cloud_file: str = BASE_CLOUD_FILE,
) -> list[dict[str, Any]]:
    if form_items_path is None:
        form_items_path = load_app_config().paths.artifacts_dir / "12_gongkan_form_analysis" / "form_items.jsonl"
    row_set = set(rows)
    items = [
        item
        for item in read_jsonl(form_items_path)
        if item.get("file_name") == base_cloud_file and int(item.get("row_index")) in row_set
    ]
    by_row = {int(item["row_index"]): item for item in items}
    missing = [row for row in rows if row not in by_row]
    if missing:
        raise RuntimeError(f"missing base cloud form rows: {missing}")
    return [by_row[row] for row in rows]


def build_masked_query(item: dict[str, Any], target_namespace: str) -> str:
    parts = [
        f"目标机房：{target_namespace}",
        "任务：为基地云机房信息调研表生成最后一列“机房信息”的候选答案",
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
        proc = subprocess.run(
            [
                "curl",
                "--noproxy",
                "*",
                "-sS",
                "--max-time",
                str(timeout),
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
            ],
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip() or proc.stdout.strip()}")
    response = json.loads(proc.stdout)
    content = response["choices"][0]["message"]["content"].strip()
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
