from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


DEFAULT_HINTS = Path(__file__).resolve().parents[1] / "artifacts/08_llm_structure_hint/table_structure_hints.jsonl"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/08_llm_structure_hint"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def display_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def pair_values(headers: list[str], values: list[str]) -> str:
    pairs: list[str] = []
    for index, value in enumerate(values):
        if not display_text(value):
            continue
        key = headers[index] if index < len(headers) and display_text(headers[index]) else f"列{index + 1}"
        pairs.append(f"{key}：{display_text(value)}")
    return "；".join(pairs)


def looks_like_checkbox_option(value: Any) -> bool:
    return display_text(value).startswith("□")


def normalize_row_sets(
    hint: dict[str, Any],
    row_by_index: dict[int, dict[str, Any]],
) -> tuple[set[int], set[int], list[int]]:
    data_rows = set(hint.get("data_rows", []))
    group_rows = set(hint.get("group_rows", []))
    normalized_group_rows: list[int] = []
    headers_text = " ".join(display_text(item) for item in hint.get("column_headers", []))
    table_type = display_text(hint.get("table_type"))
    is_questionnaire_matrix = "问卷" in table_type or "满意" in headers_text
    if not is_questionnaire_matrix:
        return data_rows, group_rows, normalized_group_rows

    for row_index in sorted(data_rows):
        values = [display_text(item) for item in row_by_index.get(row_index, {}).get("row_values") or []]
        checkbox_count = sum(1 for value in values[1:] if looks_like_checkbox_option(value))
        if values and values[0].endswith("方面") and checkbox_count >= 3:
            data_rows.remove(row_index)
            group_rows.add(row_index)
            normalized_group_rows.append(row_index)
    return data_rows, group_rows, normalized_group_rows


def stable_table_type(record: dict[str, Any], hint: dict[str, Any]) -> str:
    return display_text(record.get("table_category")) or display_text(hint.get("table_type"))


def run(hints_path: Path = DEFAULT_HINTS, out_dir: Path = DEFAULT_OUT_DIR) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = read_jsonl(hints_path)
    out: list[dict[str, Any]] = []
    for record in records:
        hint = record.get("llm_hint") or {}
        if record.get("validation", {}).get("status") != "valid":
            continue
        headers = [display_text(item) for item in hint.get("column_headers", [])]
        title_rows = set(hint.get("title_rows", []))
        context_rows = set(hint.get("context_rows", []))
        row_by_index = {row["row_index"]: row for row in record.get("table_rows", [])}
        data_rows, group_rows, normalized_group_rows = normalize_row_sets(hint, row_by_index)
        table_type = stable_table_type(record, hint)
        llm_table_type = display_text(hint.get("table_type"))
        current_group = ""
        for row_index in sorted(row_by_index):
            row = row_by_index[row_index]
            values = [display_text(item) for item in row.get("row_values") or []]
            row_label = ""
            if row_index in group_rows and values:
                current_group = values[0]
                row_label = current_group
            if row_index not in data_rows:
                continue
            body = pair_values(headers, values) if headers else display_text(row.get("current_text"))
            parts = [
                f"表格类型：{table_type}。" if table_type else "",
                f"结构类型：{llm_table_type}。" if llm_table_type and llm_table_type != table_type else "",
                f"上下文：{display_text(hint.get('context'))}。" if display_text(hint.get("context")) else "",
                f"分组：{current_group}。" if current_group and current_group != values[0] else "",
                f"内容：{body}。",
            ]
            segment_id = f"hintseg_{record['hint_id']}_row_{row_index:04d}"
            out.append(
                {
                    "segment_id": segment_id,
                    "segment_type": "llm_hint_table_row",
                    "source_hint_id": record["hint_id"],
                    "model": record["model"],
                    "file_name": record["file_name"],
                    "anchor": record["anchor"],
                    "table_no": record["table_no"],
                    "row_index": row_index,
                    "table_type": table_type,
                    "llm_table_type": llm_table_type,
                    "row_strategy": hint.get("row_strategy"),
                    "column_headers": headers,
                    "row_values": values,
                    "group": row_label or current_group,
                    "raw_text": body,
                    "embedding_text": "".join(parts),
                    "source_policy": "llm_structure_hint_only; content_from_original_rows",
                    "ignored_hint_rows": {
                        "title_rows": sorted(title_rows),
                        "context_rows": sorted(context_rows),
                        "group_rows": sorted(group_rows),
                    },
                    "postprocess": {
                        "matrix_group_rows_from_values": normalized_group_rows,
                    },
                }
            )
    write_jsonl(out_dir / "hinted_table_segments.jsonl", out)
    write_visualization(out_dir / "hinted_table_segments.md", out)
    return out


def write_visualization(path: Path, records: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# Step 08 应用 LLM 结构 Hint 后的重切样例\n")
    lines.append("这些 segment 的正文来自原始表格行；DeepSeek 只提供表头、数据行、分组行等结构 hint。\n")
    lines.append("| file | anchor | row | type | text |")
    lines.append("|---|---|---:|---|---|")
    for item in records[:80]:
        text = display_text(item.get("embedding_text"))
        if len(text) > 180:
            text = text[:179] + "..."
        escaped_text = text.replace("|", "\\|")
        lines.append(
            f"| `{display_text(item.get('file_name'))}` | `{display_text(item.get('anchor'))}` | "
            f"{item.get('row_index')} | {display_text(item.get('table_type'))} | {escaped_text} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    segments = run()
    print(f"applied {len(segments)} hinted table row segments -> {DEFAULT_OUT_DIR}")
