from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from nested_doc_rag.config import load_app_config

DEFAULT_CONFIG = load_app_config()
PROJECT_ROOT = DEFAULT_CONFIG.paths.project_root
DEFAULT_STRUCTURE_DIR = DEFAULT_CONFIG.paths.artifacts_dir / "04a_structure_parse"
DEFAULT_OUT_DIR = DEFAULT_CONFIG.paths.artifacts_dir / "12_gongkan_form_analysis"
DEFAULT_LLM_URL = DEFAULT_CONFIG.services.chat_endpoint
DEFAULT_LLM_MODEL = DEFAULT_CONFIG.services.chat_model
DEFAULT_API_KEY_ENV = DEFAULT_CONFIG.services.chat_api_key_env
DEFAULT_TIMEOUT = DEFAULT_CONFIG.evaluation.timeout_seconds

CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
BLANK_MARKERS = {"", "\\", "/", "／", "-", "—", "－"}

PROOF_KEYWORDS = [
    "证明",
    "材料",
    "截图",
    "照片",
    "图",
    "报告",
    "备案",
    "证书",
    "资质",
    "验收",
    "记录",
    "提供",
    "CAD",
    "PDF",
]

ANSWER_PLACEHOLDER_RE = re.compile(r"^(待填|待填写|暂无|无|N/A|NA|/|\\|-|—)$", re.I)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def display_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if limit and len(text) > limit:
        return text[: limit - 1] + "..."
    return text


def compact_key(value: Any) -> str:
    text = display_text(value)
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    return text.lower()


def md(value: Any, limit: int = 120) -> str:
    return display_text(value, limit).replace("|", "\\|")


def stable_id(*parts: Any) -> str:
    text = "|".join(display_text(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def col_to_num(col: str) -> int:
    number = 0
    for ch in col:
        number = number * 26 + ord(ch) - ord("A") + 1
    return number


def num_to_col(number: int) -> str:
    chars: list[str] = []
    while number:
        number, rem = divmod(number - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def split_cell_ref(ref: str) -> tuple[int, int] | None:
    match = CELL_RE.match(ref or "")
    if not match:
        return None
    col, row = match.groups()
    return int(row), col_to_num(col)


def cell_ref(row: int, col: int) -> str:
    return f"{num_to_col(col)}{row}"


def expand_range(range_ref: str) -> list[str]:
    if ":" not in range_ref:
        return [range_ref]
    start, end = range_ref.split(":", 1)
    start_rc = split_cell_ref(start)
    end_rc = split_cell_ref(end)
    if not start_rc or not end_rc:
        return []
    start_row, start_col = start_rc
    end_row, end_col = end_rc
    return [
        cell_ref(row, col)
        for row in range(start_row, end_row + 1)
        for col in range(start_col, end_col + 1)
    ]


def file_records(structure_dir: Path) -> list[dict[str, Any]]:
    return read_jsonl(structure_dir / "files.jsonl")


def load_cells(structure_dir: Path, file_id: str, sheet_index: int) -> dict[int, dict[int, str]]:
    path = structure_dir / "worksheets" / f"{file_id}.{sheet_index:02d}.cells.jsonl"
    rows: dict[int, dict[int, str]] = defaultdict(dict)
    for cell in read_jsonl(path):
        rows[int(cell["row"])][int(cell["col"])] = display_text(cell.get("value") or cell.get("formula_text") or "")
    return rows


def merge_value_map(sheet_summary: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for merge in sheet_summary.get("merges") or []:
        value = display_text(merge.get("master_value"))
        if not value:
            continue
        for ref in expand_range(merge.get("range", "")):
            values[ref] = value
    return values


def inherited_cell(rows: dict[int, dict[int, str]], merge_values: dict[str, str], row: int, col: int) -> str:
    value = rows.get(row, {}).get(col, "")
    if value:
        return value
    return merge_values.get(cell_ref(row, col), "")


def max_col(rows: dict[int, dict[int, str]]) -> int:
    cols = [col for row in rows.values() for col in row]
    return max(cols) if cols else 0


def row_values(
    rows: dict[int, dict[int, str]],
    merge_values: dict[str, str],
    row: int,
    cols: range,
) -> list[str]:
    return [inherited_cell(rows, merge_values, row, col) for col in cols]


def nonblank_count(values: list[str]) -> int:
    return sum(1 for value in values if display_text(value) not in BLANK_MARKERS)


def detect_header_row(rows: dict[int, dict[int, str]], merge_values: dict[str, str], max_columns: int) -> int | None:
    best: tuple[int, int] | None = None
    for row in sorted(rows)[:15]:
        text = " ".join(row_values(rows, merge_values, row, range(1, max_columns + 1)))
        key = compact_key(text)
        score = 0
        if any(token in key for token in ["类别", "工勘项"]):
            score += 2
        if any(token in key for token in ["子项", "指标名称", "确认项", "条目", "评估内容"]):
            score += 4
        if any(token in key for token in ["实际情况", "机房信息", "实际信息", "应答", "满足情况", "机房现状"]):
            score += 4
        if any(token in key for token in ["答复示例", "示例", "填写说明", "参考内容", "备注"]):
            score += 2
        if score:
            candidate = (score, row)
            if best is None or candidate > best:
                best = candidate
    return best[1] if best and best[0] >= 5 else None


def classify_header_cell(text: str) -> str | None:
    key = compact_key(text)
    if not key:
        return None
    if key in {"序号", "编号"}:
        return "sequence"
    if key in {"类别", "工勘项"}:
        return "category"
    if key in {"子类"}:
        return "subcategory"
    if any(token in key for token in ["子项", "指标名称", "确认项", "条目", "评估内容"]):
        return "question"
    if any(token in key for token in ["填写说明", "参考内容", "标准"]):
        return "instruction"
    if any(token in key for token in ["答复示例", "示例"]):
        return "answer_sample"
    if any(token in key for token in ["实际情况", "机房信息", "实际信息", "应答"]):
        return "answer_target"
    if "满足情况" in key:
        return "status"
    if "机房现状" in key:
        return "current_info"
    if "确认人" in key:
        return "owner"
    if any(token in key for token in ["备注", "说明"]):
        return "remark"
    if any(token in key for token in ["风险描述", "风险类别", "风险等级", "影响程度", "解决方案", "进展"]):
        return "risk_field"
    return None


def infer_column_roles(
    rows: dict[int, dict[int, str]],
    merge_values: dict[str, str],
    header_row: int | None,
    max_columns: int,
) -> dict[str, list[int]]:
    roles: dict[str, list[int]] = defaultdict(list)
    if header_row is not None:
        for col in range(1, max_columns + 1):
            role = classify_header_cell(inherited_cell(rows, merge_values, header_row, col))
            if role:
                roles[role].append(col)
    return dict(roles)


def infer_sheet_kind(
    sheet_name: str,
    roles: dict[str, list[int]],
    rows: dict[int, dict[int, str]],
    merge_values: dict[str, str],
    max_columns: int,
) -> str:
    name_key = compact_key(sheet_name)
    if any(token in name_key for token in ["照片", "平面图", "布局图"]):
        return "evidence_or_layout_sheet"
    if "风险" in name_key:
        return "risk_register"
    if "不满足项" in name_key or "部分满足项" in name_key:
        return "filtered_assessment_findings"
    if roles.get("status") and roles.get("current_info"):
        return "assessment_matrix"
    if roles.get("answer_target") and roles.get("question"):
        return "row_question_answer_form"
    first_rows = [
        row_values(rows, merge_values, row, range(1, min(max_columns, 5) + 1))
        for row in sorted(rows)[:10]
    ]
    if sum(1 for values in first_rows if nonblank_count(values[:2]) >= 2) >= 4:
        return "key_value_form"
    return "non_fill_grid"


def has_proof_signal(*values: str) -> bool:
    text = " ".join(display_text(value) for value in values)
    return any(keyword.lower() in text.lower() for keyword in PROOF_KEYWORDS)


def answer_state(value: str) -> str:
    text = display_text(value)
    if not text:
        return "empty"
    if ANSWER_PLACEHOLDER_RE.match(text):
        return "placeholder"
    return "has_value"


def suggest_rag_format(sheet_kind: str, roles: dict[str, list[int]], needs_evidence: bool) -> dict[str, Any]:
    if sheet_kind in {"evidence_or_layout_sheet", "non_fill_grid"}:
        return {
            "mode": "evidence_or_layout_reference",
            "return_fields": ["summary", "source_chunks", "evidence_attachments", "notes"],
            "answer_style": "不直接回填单元格；作为图纸、照片、机柜布局等佐证或人工参考。",
            "needs_evidence": True,
        }
    if sheet_kind == "assessment_matrix":
        return {
            "mode": "status_with_current_info",
            "return_fields": ["status", "current_info", "confidence", "source_chunks", "evidence_attachments", "notes"],
            "answer_style": "返回满足/部分满足/不满足，并给出机房现状原文；不能只返回一个状态。",
            "needs_evidence": needs_evidence,
        }
    if sheet_kind == "risk_register":
        return {
            "mode": "risk_row",
            "return_fields": ["risk_description", "risk_level", "impact", "mitigation", "deadline", "source_chunks", "notes"],
            "answer_style": "按风险登记表字段返回结构化对象，缺字段返回未找到。",
            "needs_evidence": needs_evidence,
        }
    if sheet_kind == "filtered_assessment_findings":
        return {
            "mode": "finding_row",
            "return_fields": ["category", "question", "status", "current_info", "source_chunks", "notes"],
            "answer_style": "用于不满足/部分满足列表，保留类别、评估内容、满足情况和机房现状。",
            "needs_evidence": needs_evidence,
        }
    return {
        "mode": "cell_answer",
        "return_fields": ["answer_value", "confidence", "source_chunks", "evidence_attachments", "notes"],
        "answer_style": "返回可直接写入目标单元格的短答案；必要时附证据，不把证据文字混入单元格答案。",
        "needs_evidence": needs_evidence,
    }


def build_sheet_profile(
    file_record: dict[str, Any],
    sheet_summary: dict[str, Any],
    structure_dir: Path,
) -> dict[str, Any]:
    rows = load_cells(structure_dir, file_record["file_id"], sheet_summary["sheet_index"])
    merge_values = merge_value_map(sheet_summary)
    max_columns = max_col(rows)
    header_row = detect_header_row(rows, merge_values, max_columns)
    roles = infer_column_roles(rows, merge_values, header_row, max_columns)
    sheet_kind = infer_sheet_kind(sheet_summary["sheet_name"], roles, rows, merge_values, max_columns)
    data_start = (header_row + 1) if header_row else None
    data_end = max(rows) if rows else None

    proof_rows = 0
    filled_targets = 0
    empty_targets = 0
    target_cols = roles.get("answer_target") or roles.get("status") or []
    question_cols = roles.get("question") or []
    instruction_cols = roles.get("instruction") or []
    remark_cols = roles.get("remark") or []
    for row in sorted(rows):
        if header_row and row <= header_row:
            continue
        q_text = " ".join(inherited_cell(rows, merge_values, row, col) for col in question_cols + instruction_cols + remark_cols)
        if has_proof_signal(q_text):
            proof_rows += 1
        for col in target_cols:
            state = answer_state(inherited_cell(rows, merge_values, row, col))
            if state == "has_value":
                filled_targets += 1
            elif state in {"empty", "placeholder"}:
                empty_targets += 1

    needs_evidence = proof_rows > 0 or sheet_kind == "evidence_or_layout_sheet"
    return {
        "file_id": file_record["file_id"],
        "file_name": file_record["file_name"],
        "relative_path": file_record["relative_path"],
        "sheet_index": sheet_summary["sheet_index"],
        "sheet_name": sheet_summary["sheet_name"],
        "actual_dimension": sheet_summary.get("actual_dimension"),
        "non_empty_cell_count": sheet_summary.get("non_empty_cell_count"),
        "merge_count": sheet_summary.get("merge_count"),
        "attachment_count": sheet_summary.get("attachment_count"),
        "header_row": header_row,
        "data_start_row": data_start,
        "data_end_row": data_end,
        "sheet_kind": sheet_kind,
        "column_roles": {role: [num_to_col(col) for col in cols] for role, cols in roles.items()},
        "target_answer_columns": [num_to_col(col) for col in roles.get("answer_target", [])],
        "sample_answer_columns": [num_to_col(col) for col in roles.get("answer_sample", [])],
        "status_columns": [num_to_col(col) for col in roles.get("status", [])],
        "current_info_columns": [num_to_col(col) for col in roles.get("current_info", [])],
        "proof_signal_row_count": proof_rows,
        "target_cells_with_value": filled_targets,
        "target_cells_empty_or_placeholder": empty_targets,
        "needs_evidence": needs_evidence,
        "rag_return_format": suggest_rag_format(sheet_kind, roles, needs_evidence),
    }


def category_path_for_row(
    rows: dict[int, dict[int, str]],
    merge_values: dict[str, str],
    row: int,
    roles: dict[str, list[int]],
) -> list[str]:
    path: list[str] = []
    for role in ["category", "subcategory"]:
        for col in roles.get(role, []):
            value = inherited_cell(rows, merge_values, row, col)
            if value and value not in path:
                path.append(value)
    return path


def first_value(
    rows: dict[int, dict[int, str]],
    merge_values: dict[str, str],
    row: int,
    cols: list[int],
) -> str:
    for col in cols:
        value = inherited_cell(rows, merge_values, row, col)
        if value:
            return value
    return ""


def make_query_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("question_text"),
        item.get("instruction_text"),
        item.get("answer_example"),
        " / ".join(item.get("category_path") or []),
    ]
    return " ".join(display_text(part) for part in parts if display_text(part))


def extract_form_items(
    file_record: dict[str, Any],
    sheet_summary: dict[str, Any],
    profile: dict[str, Any],
    structure_dir: Path,
) -> list[dict[str, Any]]:
    rows = load_cells(structure_dir, file_record["file_id"], sheet_summary["sheet_index"])
    merge_values = merge_value_map(sheet_summary)
    header_row = profile.get("header_row")
    roles_by_letter = profile.get("column_roles") or {}
    roles: dict[str, list[int]] = {
        role: [col_to_num(col) for col in cols]
        for role, cols in roles_by_letter.items()
    }
    sheet_kind = profile["sheet_kind"]

    if sheet_kind in {"evidence_or_layout_sheet", "non_fill_grid"}:
        return []

    items: list[dict[str, Any]] = []
    if sheet_kind == "key_value_form":
        for row in sorted(rows):
            key = inherited_cell(rows, merge_values, row, 1)
            value = inherited_cell(rows, merge_values, row, 2)
            if not key or row == 1:
                continue
            item = {
                "form_item_id": "gkitem_" + stable_id(file_record["file_id"], sheet_summary["sheet_index"], row, "kv"),
                "file_id": file_record["file_id"],
                "file_name": file_record["file_name"],
                "relative_path": file_record["relative_path"],
                "sheet_index": sheet_summary["sheet_index"],
                "sheet_name": sheet_summary["sheet_name"],
                "row_index": row,
                "category_path": [],
                "question_text": key,
                "instruction_text": "",
                "answer_example": "",
                "target_cell": cell_ref(row, 2),
                "existing_value": value,
                "answer_state": answer_state(value),
                "needs_evidence": has_proof_signal(key, value),
                "evidence_reason": "字段或值包含证明/图纸/照片/记录类词" if has_proof_signal(key, value) else "",
                "rag_return_format": suggest_rag_format(sheet_kind, roles, has_proof_signal(key, value)),
            }
            item["suggested_retrieval_query"] = make_query_text(item)
            items.append(item)
        return items

    question_cols = roles.get("question", [])
    target_cols = roles.get("answer_target") or roles.get("status") or roles.get("current_info") or []
    instruction_cols = roles.get("instruction", [])
    sample_cols = roles.get("answer_sample", [])
    remark_cols = roles.get("remark", [])
    current_info_cols = roles.get("current_info", [])
    status_cols = roles.get("status", [])
    data_start = (int(header_row) + 1) if header_row else 1

    for row in range(data_start, (max(rows) if rows else 0) + 1):
        question = first_value(rows, merge_values, row, question_cols)
        if not question and sheet_kind != "risk_register":
            continue
        category_path = category_path_for_row(rows, merge_values, row, roles)
        instruction = first_value(rows, merge_values, row, instruction_cols)
        answer_example = first_value(rows, merge_values, row, sample_cols)
        remark = first_value(rows, merge_values, row, remark_cols)
        status = first_value(rows, merge_values, row, status_cols)
        current_info = first_value(rows, merge_values, row, current_info_cols)
        target_col = target_cols[0] if target_cols else None
        existing_value = inherited_cell(rows, merge_values, row, target_col) if target_col else ""
        proof_signal = has_proof_signal(question, instruction, answer_example, remark, current_info)
        item = {
            "form_item_id": "gkitem_" + stable_id(file_record["file_id"], sheet_summary["sheet_index"], row, question),
            "file_id": file_record["file_id"],
            "file_name": file_record["file_name"],
            "relative_path": file_record["relative_path"],
            "sheet_index": sheet_summary["sheet_index"],
            "sheet_name": sheet_summary["sheet_name"],
            "row_index": row,
            "category_path": category_path,
            "question_text": question,
            "instruction_text": instruction or remark,
            "answer_example": answer_example,
            "target_cell": cell_ref(row, target_col) if target_col else None,
            "existing_value": existing_value,
            "status_value": status,
            "current_info": current_info,
            "answer_state": answer_state(existing_value),
            "needs_evidence": proof_signal,
            "evidence_reason": "题目/说明/示例/现状包含证明、图纸、照片、报告、证书或记录要求" if proof_signal else "",
            "rag_return_format": suggest_rag_format(sheet_kind, roles, proof_signal),
        }
        item["suggested_retrieval_query"] = make_query_text(item)
        items.append(item)
    return items


def sample_rows_for_agent(
    rows: dict[int, dict[int, str]],
    merge_values: dict[str, str],
    max_columns: int,
    header_row: int | None,
) -> list[dict[str, Any]]:
    row_numbers = set(sorted(rows)[:8])
    if header_row:
        row_numbers.update(range(max(1, header_row - 1), min(max(rows), header_row + 12) + 1))
    for row in sorted(rows):
        text = " ".join(inherited_cell(rows, merge_values, row, col) for col in range(1, max_columns + 1))
        if has_proof_signal(text):
            row_numbers.add(row)
        if len(row_numbers) >= 24:
            break
    sample: list[dict[str, Any]] = []
    for row in sorted(row_numbers):
        cells: dict[str, str] = {}
        for col in range(1, min(max_columns, 20) + 1):
            value = inherited_cell(rows, merge_values, row, col)
            if value:
                cells[num_to_col(col)] = display_text(value, 120)
        if cells:
            sample.append({"row": row, "cells": cells})
    return sample


def build_agent_prompt(profile: dict[str, Any], sample_rows: list[dict[str, Any]]) -> str:
    schema = {
        "sheet_kind": "row_question_answer_form | assessment_matrix | key_value_form | evidence_or_layout_sheet | risk_register | filtered_assessment_findings | non_fill_grid",
        "shape_summary": "一句话说明表格形状",
        "header_rows": [1],
        "data_rows": [2, 3],
        "given_info_columns": ["E"],
        "question_columns": ["B"],
        "answer_target_columns": ["C"],
        "answer_sample_columns": ["D"],
        "status_columns": ["C"],
        "evidence_requirement": {
            "has_evidence_requirement": False,
            "evidence_columns": [],
            "evidence_signal_columns": [],
            "rule": "哪些词或列说明需要证明材料",
        },
        "rag_return_format": {
            "mode": "cell_answer | status_with_current_info | evidence_or_layout_reference | risk_row | finding_row",
            "fields": ["answer_value", "confidence", "source_chunks", "evidence_attachments", "notes"],
            "answer_style": "如何组织 RAG 返回值",
        },
        "agent_needed_next": False,
        "confidence": 0.9,
        "notes": "只讲结构，不写答案",
    }
    source = {
        "file_name": profile["file_name"],
        "sheet_name": profile["sheet_name"],
        "actual_dimension": profile["actual_dimension"],
        "deterministic_profile": {
            "sheet_kind": profile["sheet_kind"],
            "header_row": profile["header_row"],
            "column_roles": profile["column_roles"],
            "needs_evidence": profile["needs_evidence"],
            "rag_return_format": profile["rag_return_format"],
        },
    }
    return (
        "你是工勘单表格结构判定智能体。请只输出严格 JSON，不要 Markdown，不要解释。\n"
        "你的任务只包括判断表格形状、列角色、哪些信息已给出、哪些是待回答问题、是否有回答样例、是否需要证明材料，以及 RAG 返回值应是什么格式。\n"
        "你不能生成或改写工勘答案，不能补充原文没有的信息。答案内容后续必须由 RAG 检索原始知识库生成。\n\n"
        "输出 JSON schema 示例：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "判定规则：\n"
        "1. answer_target_columns 是可写入或可生成答案的列。\n"
        "2. answer_sample_columns 是示例，不应当作为目标答案直接复制。\n"
        "3. given_info_columns 是表中已经给出的现状、机房信息、满足情况、风险项等列。\n"
        "4. evidence_requirement.has_evidence_requirement 只表示该 sheet 或部分行存在证据/证明材料要求。\n"
        "5. rag_return_format.fields 必须便于代码消费，字段名用英文 snake_case。\n\n"
        f"表格来源和确定性初判：\n{json.dumps(source, ensure_ascii=False, indent=2)}\n\n"
        f"抽样行：\n{json.dumps(sample_rows, ensure_ascii=False, indent=2)}\n"
    )


def call_llm_json(
    *,
    url: str,
    model: str,
    api_key: str,
    prompt: str,
    timeout: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "你只做工勘单结构判断，必须输出严格 JSON。"},
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
                "-H",
                f"Authorization: Bearer {api_key}",
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
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    if not content.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            content = match.group(0)
    return json.loads(content)


def prompt_hash(prompt: str, model: str) -> str:
    return hashlib.sha256((model + "\n" + prompt).encode("utf-8")).hexdigest()[:16]


def build_agent_hints(
    profiles: list[dict[str, Any]],
    structure_dir: Path,
    out_dir: Path,
    *,
    url: str,
    model: str,
    api_key_env: str,
    timeout: int,
    use_llm: bool,
) -> list[dict[str, Any]]:
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get(api_key_env)
    hints: list[dict[str, Any]] = []
    file_by_id = {record["file_id"]: record for record in file_records(structure_dir)}
    if use_llm and not api_key:
        raise RuntimeError(f"missing API key env: {api_key_env}")

    for profile in profiles:
        file_record = file_by_id[profile["file_id"]]
        sheet_summary = next(s for s in file_record["sheets"] if s["sheet_index"] == profile["sheet_index"])
        rows = load_cells(structure_dir, file_record["file_id"], sheet_summary["sheet_index"])
        merge_values = merge_value_map(sheet_summary)
        sample_rows = sample_rows_for_agent(rows, merge_values, max_col(rows), profile.get("header_row"))
        prompt = build_agent_prompt(profile, sample_rows)
        cache_key = prompt_hash(prompt, model)
        cache_path = cache_dir / f"{cache_key}.json"
        if use_llm:
            if cache_path.exists():
                llm_hint = json.loads(cache_path.read_text(encoding="utf-8"))
            else:
                llm_hint = call_llm_json(url=url, model=model, api_key=api_key or "", prompt=prompt, timeout=timeout)
                write_json(cache_path, llm_hint)
            source = "deepseek"
        else:
            llm_hint = {
                "sheet_kind": profile["sheet_kind"],
                "shape_summary": "未调用智能体，使用确定性结构初判。",
                "header_rows": [profile["header_row"]] if profile.get("header_row") else [],
                "data_rows": [],
                "given_info_columns": profile.get("target_answer_columns") or profile.get("current_info_columns") or [],
                "question_columns": profile.get("column_roles", {}).get("question", []),
                "answer_target_columns": profile.get("target_answer_columns", []),
                "answer_sample_columns": profile.get("sample_answer_columns", []),
                "status_columns": profile.get("status_columns", []),
                "evidence_requirement": {
                    "has_evidence_requirement": profile["needs_evidence"],
                    "evidence_columns": [],
                    "evidence_signal_columns": [],
                    "rule": "确定性关键词判断",
                },
                "rag_return_format": profile["rag_return_format"],
                "agent_needed_next": profile["sheet_kind"] in {"evidence_or_layout_sheet", "non_fill_grid"},
                "confidence": 0.65,
                "notes": "deterministic fallback",
            }
            source = "deterministic_fallback"
        hints.append(
            {
                "file_id": profile["file_id"],
                "file_name": profile["file_name"],
                "sheet_index": profile["sheet_index"],
                "sheet_name": profile["sheet_name"],
                "hint_source": source,
                "prompt_hash": cache_key,
                "agent_hint": llm_hint,
            }
        )
    return hints


def write_visualization(
    out_dir: Path,
    profiles: list[dict[str, Any]],
    hints: list[dict[str, Any]],
    items: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    hint_by_sheet = {
        (hint["file_id"], hint["sheet_index"]): hint["agent_hint"]
        for hint in hints
    }
    item_counts = Counter((item["file_id"], item["sheet_index"]) for item in items)
    lines: list[str] = []
    lines.append("# Step 12 工勘单结构分析\n")
    lines.append("本步骤只分析待填写工勘单的表格形状和 RAG 返回格式，不生成最终填报答案。\n")
    lines.append("## 总览\n")
    lines.append(f"- 工勘单文件数：**{summary['survey_file_count']}**")
    lines.append(f"- Sheet 数：**{summary['sheet_count']}**")
    lines.append(f"- 可抽取填报项：**{summary['form_item_count']}**")
    lines.append(f"- 需要证明/图片/报告类佐证的填报项：**{summary['items_needing_evidence']}**")
    lines.append(f"- 智能体 hint 数：**{summary['agent_hint_count']}**\n")

    lines.append("## Sheet 形状\n")
    lines.append("| file | sheet | kind | header | roles | items | agent shape | RAG mode | evidence |")
    lines.append("|---|---|---|---:|---|---:|---|---|---|")
    for profile in profiles:
        hint = hint_by_sheet.get((profile["file_id"], profile["sheet_index"]), {})
        roles = ", ".join(f"{k}={v}" for k, v in profile["column_roles"].items())
        rag_mode = (hint.get("rag_return_format") or profile["rag_return_format"]).get("mode")
        lines.append(
            f"| {md(profile['file_name'], 34)} | {md(profile['sheet_name'], 30)} | `{profile['sheet_kind']}` | "
            f"{profile.get('header_row') or ''} | {md(roles, 80)} | "
            f"{item_counts[(profile['file_id'], profile['sheet_index'])]} | "
            f"{md(hint.get('shape_summary'), 70)} | `{md(rag_mode, 30)}` | "
            f"{'yes' if profile['needs_evidence'] else 'no'} |"
        )

    lines.append("\n## RAG 返回格式建议\n")
    format_counts = Counter((item["rag_return_format"] or {}).get("mode") for item in items)
    lines.append("| mode | item_count | usage |")
    lines.append("|---|---:|---|")
    mode_usage = {
        "cell_answer": "返回可写入单元格的短答案，附 source_chunks/evidence_attachments。",
        "status_with_current_info": "返回满足状态和机房现状，适用于评估矩阵。",
        "finding_row": "返回不满足/部分满足条目字段。",
        "risk_row": "返回风险登记字段。",
    }
    for mode, count in sorted(format_counts.items()):
        lines.append(f"| `{mode}` | {count} | {mode_usage.get(mode or '', '')} |")

    lines.append("\n## 填报项样例\n")
    lines.append("| file | sheet | row | target | question | example/current | evidence | suggested query |")
    lines.append("|---|---|---:|---|---|---|---|---|")
    for item in items[:40]:
        example = item.get("answer_example") or item.get("existing_value") or item.get("current_info")
        lines.append(
            f"| {md(item['file_name'], 28)} | {md(item['sheet_name'], 22)} | {item['row_index']} | "
            f"`{item.get('target_cell') or ''}` | {md(item.get('question_text'), 70)} | "
            f"{md(example, 80)} | {'yes' if item.get('needs_evidence') else 'no'} | "
            f"{md(item.get('suggested_retrieval_query'), 100)} |"
        )

    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_rag_return_contract() -> dict[str, Any]:
    return {
        "contract_name": "gongkan_rag_answer_v1",
        "principle": [
            "RAG 返回内容必须来自检索到的知识库 chunk 或证据附件；不能补编。",
            "answer_value/status/current_info 等可写字段与 source_chunks/evidence_attachments 分离。",
            "当证据不足时，填写 missing_fields 和 notes，不强行生成答案。",
            "图片不 OCR；只在 evidence_attachments 中作为佐证返回。",
        ],
        "common_fields": {
            "form_item_id": "Step 12 form_items.jsonl 中的填报项 ID",
            "file_name": "目标工勘单文件名",
            "sheet_name": "目标 sheet",
            "target_cell": "可写入单元格；非单元格回填场景可为 null",
            "mode": "cell_answer | status_with_current_info | finding_row | risk_row",
            "query": "用于检索的规范化问题",
            "confidence": "0-1；基于检索分数、rerank 分数和证据一致性",
            "source_chunks": [
                {
                    "chunk_id": "知识库 chunk id",
                    "namespace": "机房分库，如 xixian_6/global",
                    "source_type": "main_excel_capability | embedded_word_table",
                    "anchor": "源文件位置",
                    "raw_text": "用于回答的原文片段",
                    "score": "召回或 rerank 分数",
                }
            ],
            "evidence_attachments": [
                {
                    "attachment_id": "图片/附件 ID",
                    "source_anchor": "证据在源文件中的位置",
                    "evidence_role": "proof_image | layout | report | certificate | other",
                }
            ],
            "missing_fields": ["证据不足或原文未找到的字段"],
            "notes": "边界说明，例如“原文未给出路由策略命令”。",
        },
        "modes": {
            "cell_answer": {
                "write_behavior": "写入单个目标单元格。",
                "required_fields": ["answer_value", "confidence", "source_chunks"],
                "payload": {
                    "answer_value": "短答案；避免把引用和证据说明混入单元格",
                    "answer_unit": "单位，如 kW/kVA/℃；没有则为空",
                    "answer_basis": "一句话说明答案来自哪些事实",
                },
            },
            "status_with_current_info": {
                "write_behavior": "适用于评估矩阵；通常需要同时写满足情况和机房现状。",
                "required_fields": ["status", "current_info", "confidence", "source_chunks"],
                "payload": {
                    "status": "满足 | 部分满足 | 不满足 | 不涉及 | 未找到",
                    "current_info": "机房现状原文式描述",
                    "status_reason": "判定状态的依据",
                },
            },
            "finding_row": {
                "write_behavior": "适用于不满足项/部分满足项清单；返回整行字段。",
                "required_fields": ["category", "question", "status", "current_info", "source_chunks"],
                "payload": {
                    "category": "问题类别",
                    "question": "评估内容",
                    "status": "不满足 | 部分满足",
                    "current_info": "机房现状",
                },
            },
            "risk_row": {
                "write_behavior": "适用于风险登记表；返回风险整行字段。",
                "required_fields": ["risk_description", "risk_level", "impact", "mitigation", "source_chunks"],
                "payload": {
                    "room_or_building": "机房/楼栋",
                    "risk_description": "风险描述",
                    "risk_category": "风险类别",
                    "risk_level": "风险等级",
                    "impact": "影响程度及范围",
                    "mitigation": "解决方案/应急措施",
                    "deadline": "解决时间",
                    "progress": "进展",
                },
            },
        },
    }


def run(
    structure_dir: Path = DEFAULT_STRUCTURE_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    use_llm: bool = False,
    llm_url: str = DEFAULT_LLM_URL,
    llm_model: str = DEFAULT_LLM_MODEL,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    survey_files = [record for record in file_records(structure_dir) if record.get("document_role") == "survey_form"]
    profiles: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for file_record in survey_files:
        for sheet_summary in file_record.get("sheets") or []:
            profile = build_sheet_profile(file_record, sheet_summary, structure_dir)
            profiles.append(profile)
            items.extend(extract_form_items(file_record, sheet_summary, profile, structure_dir))

    hints = build_agent_hints(
        profiles,
        structure_dir,
        out_dir,
        url=llm_url,
        model=llm_model,
        api_key_env=api_key_env,
        timeout=timeout,
        use_llm=use_llm,
    )

    summary = {
        "survey_file_count": len(survey_files),
        "sheet_count": len(profiles),
        "form_item_count": len(items),
        "items_needing_evidence": sum(1 for item in items if item.get("needs_evidence")),
        "agent_hint_count": len(hints),
        "agent_backend": "deepseek" if use_llm else "deterministic_fallback",
        "counts_by_sheet_kind": dict(Counter(profile["sheet_kind"] for profile in profiles)),
        "item_counts_by_file": dict(Counter(item["file_name"] for item in items)),
        "item_counts_by_answer_state": dict(Counter(item["answer_state"] for item in items)),
        "rag_format_counts": dict(Counter((item["rag_return_format"] or {}).get("mode") for item in items)),
        "principle": "智能体只判断结构和返回格式；答案内容必须由 RAG 从知识库和证据附件生成。",
    }

    write_jsonl(out_dir / "sheet_profiles.jsonl", profiles)
    write_jsonl(out_dir / "agent_sheet_hints.jsonl", hints)
    write_jsonl(out_dir / "form_items.jsonl", items)
    write_json(out_dir / "rag_return_contract.json", build_rag_return_contract())
    write_json(out_dir / "summary.json", summary)
    write_visualization(out_dir, profiles, hints, items, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 12: analyze survey/gongkan forms and decide RAG return formats.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--structure-dir", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--use-llm", action="store_true", default=None)
    parser.add_argument("--llm-url", default=None)
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args()
    config = load_app_config(args.config)
    structure_dir = args.structure_dir or (config.paths.artifacts_dir / "04a_structure_parse")
    out_dir = args.out_dir or (config.paths.artifacts_dir / "12_gongkan_form_analysis")
    summary = run(
        structure_dir,
        out_dir,
        use_llm=bool(args.use_llm),
        llm_url=args.llm_url or config.services.chat_endpoint,
        llm_model=args.llm_model or config.services.chat_model,
        api_key_env=args.api_key_env or config.services.chat_api_key_env,
        timeout=args.timeout or config.evaluation.timeout_seconds,
    )
    print(
        f"analyzed {summary['survey_file_count']} gongkan files, "
        f"{summary['sheet_count']} sheets, {summary['form_item_count']} form items -> {out_dir}"
    )


if __name__ == "__main__":
    main()
