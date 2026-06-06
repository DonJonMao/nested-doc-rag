from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_STRUCTURE_DIR = Path("/Users/mao/projects/datacenter/artifacts/04a_structure_parse")
DEFAULT_OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/05_segment_extract")

CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")


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


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u3000", " ")
    text = re.sub(r"\s+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    return text


def display_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def col_to_num(col: str) -> int:
    num = 0
    for ch in col:
        num = num * 26 + ord(ch) - ord("A") + 1
    return num


def num_to_col(num: int) -> str:
    chars: list[str] = []
    while num:
        num, rem = divmod(num - 1, 26)
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
    return [cell_ref(row, col) for row in range(start_row, end_row + 1) for col in range(start_col, end_col + 1)]


def file_records(structure_dir: Path) -> list[dict[str, Any]]:
    return read_jsonl(structure_dir / "files.jsonl")


def load_cells(structure_dir: Path, file_id: str, sheet_index: int) -> dict[int, dict[int, dict[str, Any]]]:
    path = structure_dir / "worksheets" / f"{file_id}.{sheet_index:02d}.cells.jsonl"
    by_row: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for cell in read_jsonl(path):
        by_row[cell["row"]][cell["col"]] = cell
    return by_row


def load_sheet_summary(structure_dir: Path, file_id: str, sheet_index: int) -> dict[str, Any]:
    path = structure_dir / "worksheets" / f"{file_id}.{sheet_index:02d}.sheet.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_attachments(structure_dir: Path, file_id: str) -> list[dict[str, Any]]:
    return read_jsonl(structure_dir / "attachments" / f"{file_id}.attachments.jsonl")


def cell_value(cell: dict[str, Any] | None, include_formula: bool = False) -> str:
    if not cell:
        return ""
    value = cell.get("value")
    if value not in (None, ""):
        return str(value)
    if include_formula and cell.get("formula_text"):
        return "=" + str(cell["formula_text"])
    return ""


def detect_header_row(cells_by_row: dict[int, dict[int, dict[str, Any]]]) -> tuple[int | None, dict[str, Any]]:
    best_row: int | None = None
    best_score = 0
    best_mapping: dict[str, Any] = {}

    for row in sorted(cells_by_row)[:15]:
        mapping: dict[str, Any] = {}
        proof_cols: list[int] = []
        score = 0
        for col, cell in sorted(cells_by_row[row].items()):
            text = normalize_text(cell_value(cell))
            if not text:
                continue
            if "序号" in text:
                mapping["sequence"] = col
                score += 2
            if "类别" in text:
                mapping["category"] = col
                score += 2
            if "能力描述" in text or "指标名称" in text or "条目" in text:
                mapping["capability_desc"] = col
                score += 3
            if "是否满足" in text or "具体数值" in text or "应答" in text:
                mapping["answer_value"] = col
                score += 3
            if "证明材料" in text or ("证明" in text and "材料" in text):
                proof_cols.append(col)
                score += 2
        if proof_cols:
            mapping["proof_material"] = proof_cols
        if score > best_score:
            best_score = score
            best_row = row
            best_mapping = mapping

    required = {"sequence", "capability_desc", "answer_value"}
    if best_row is None or not required.issubset(best_mapping):
        return None, {"confidence": 0.0, "reason": "standard knowledge-base header not found"}

    confidence = min(1.0, best_score / 12)
    best_mapping["confidence"] = round(confidence, 3)
    best_mapping["reason"] = "matched standard knowledge-base header"
    return best_row, best_mapping


def merge_value_map(sheet_summary: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for merge in sheet_summary.get("merges", []):
        value = merge.get("master_value")
        if value in (None, ""):
            continue
        for ref in expand_range(merge.get("range", "")):
            values[ref] = value
    return values


def inherited_value(
    cells_by_row: dict[int, dict[int, dict[str, Any]]],
    merge_values: dict[str, Any],
    row: int,
    col: int | None,
) -> str:
    if col is None:
        return ""
    value = cell_value(cells_by_row.get(row, {}).get(col))
    if value:
        return value
    return str(merge_values.get(cell_ref(row, col), "") or "")


def max_col_from_sheet(sheet_summary: dict[str, Any], cells_by_row: dict[int, dict[int, dict[str, Any]]]) -> int:
    coords = [col for row in cells_by_row.values() for col in row]
    if coords:
        return max(coords)
    actual = sheet_summary.get("actual_max_cell")
    parsed = split_cell_ref(actual or "")
    return parsed[1] if parsed else 0


def proof_columns(mapping: dict[str, Any], sheet_summary: dict[str, Any], cells_by_row: dict[int, dict[int, dict[str, Any]]]) -> list[int]:
    explicit = list(mapping.get("proof_material", []))
    if not explicit:
        return []
    start = min(explicit)
    max_col = max_col_from_sheet(sheet_summary, cells_by_row)
    return list(range(start, max_col + 1))


def row_attachments(attachments_by_cell: dict[str, list[dict[str, Any]]], row: int, proof_cols: list[int]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for col in proof_cols:
        result.extend(attachments_by_cell.get(cell_ref(row, col), []))
    return result


def sentence_field(label: str, value: Any) -> str:
    text = display_text(value)
    text = re.sub(r"[。！？!?；;，,、.]+$", "", text)
    return f"{label}：{text}。" if text else ""


def make_embedding_text(segment: dict[str, Any]) -> str:
    parts = [
        sentence_field("数据中心", segment.get("data_center_name") or segment.get("data_center_id") or "global"),
        sentence_field("文件", segment["file_name"]),
        sentence_field("Sheet", segment["sheet_name"]),
    ]
    if segment.get("category_path"):
        parts.append(sentence_field("类别", " / ".join(segment["category_path"])))
    parts.append(sentence_field("能力描述", segment["capability_desc"]))
    parts.append(sentence_field("现状/答案", segment["answer_value"]))
    return "".join(parts)


def make_raw_text(segment: dict[str, Any]) -> str:
    parts: list[str] = []
    if segment.get("category_path"):
        parts.append(" / ".join(segment["category_path"]))
    parts.append(segment["capability_desc"])
    parts.append(segment["answer_value"])
    return " / ".join(part for part in parts if part)


def extract_from_sheet(
    structure_dir: Path,
    file_record: dict[str, Any],
    sheet_summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    file_id = file_record["file_id"]
    sheet_index = sheet_summary["sheet_index"]
    cells_by_row = load_cells(structure_dir, file_id, sheet_index)
    header_row, mapping = detect_header_row(cells_by_row)
    if header_row is None:
        return [], {
            "file_id": file_id,
            "sheet_index": sheet_index,
            "sheet_name": sheet_summary["sheet_name"],
            "mapping_status": "not_found",
            "reason": mapping["reason"],
        }

    merge_values = merge_value_map(sheet_summary)
    proof_cols = proof_columns(mapping, sheet_summary, cells_by_row)
    attachments = load_attachments(structure_dir, file_id)
    attachments_by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attachment in attachments:
        if attachment.get("sheet_name") != sheet_summary["sheet_name"]:
            continue
        source_cell = attachment.get("source_cell")
        if source_cell:
            attachments_by_cell[source_cell].append(attachment)

    seq_col = mapping.get("sequence")
    category_col = mapping.get("category")
    desc_col = mapping.get("capability_desc")
    answer_col = mapping.get("answer_value")
    data_start = header_row + 1
    data_end = max(cells_by_row) if cells_by_row else header_row
    segments: list[dict[str, Any]] = []

    for row in range(data_start, data_end + 1):
        sequence = display_text(inherited_value(cells_by_row, merge_values, row, seq_col))
        capability_desc = display_text(inherited_value(cells_by_row, merge_values, row, desc_col))
        answer_value = display_text(inherited_value(cells_by_row, merge_values, row, answer_col))
        category = display_text(inherited_value(cells_by_row, merge_values, row, category_col))

        if not capability_desc and not answer_value:
            continue
        if not sequence and not capability_desc:
            continue

        attachments_for_row = row_attachments(attachments_by_cell, row, proof_cols)
        row_cells = cells_by_row.get(row, {})
        proof_refs: list[str] = []
        for col in proof_cols:
            ref = cell_ref(row, col)
            if col in row_cells or attachments_by_cell.get(ref):
                proof_refs.append(ref)
        category_path = [category] if category else []

        segment = {
            "segment_id": f"seg_{file_id}_{sheet_index:02d}_row_{row:04d}",
            "segment_type": "excel_capability_row",
            "data_center_id": file_record.get("data_center_id") or "global",
            "data_center_name": file_record.get("data_center_id") or "global",
            "document_role": file_record["document_role"],
            "file_id": file_id,
            "file_name": file_record["file_name"],
            "relative_path": file_record["relative_path"],
            "sheet_index": sheet_index,
            "sheet_name": sheet_summary["sheet_name"],
            "row_index": row,
            "sequence": sequence,
            "category_path": category_path,
            "capability_desc": capability_desc,
            "answer_value": answer_value,
            "proof_cell_refs": proof_refs,
            "proof_attachments": attachments_for_row,
            "proof_attachment_count": len(attachments_for_row),
            "source_anchor": {
                "file_name": file_record["file_name"],
                "sheet_name": sheet_summary["sheet_name"],
                "row_index": row,
                "cell_range": f"{cell_ref(row, 1)}:{cell_ref(row, max_col_from_sheet(sheet_summary, cells_by_row))}",
                "text_cells": [
                    cell_ref(row, col)
                    for col in [category_col, desc_col, answer_col]
                    if col is not None and col in row_cells
                ],
                "proof_cells": proof_refs,
            },
            "extraction_rule": "standard_knowledge_base_header",
        }
        segment["raw_text"] = make_raw_text(segment)
        segment["embedding_text"] = make_embedding_text(segment)
        segments.append(segment)

    mapping_record = {
        "file_id": file_id,
        "file_name": file_record["file_name"],
        "relative_path": file_record["relative_path"],
        "data_center_id": file_record.get("data_center_id") or "global",
        "sheet_index": sheet_index,
        "sheet_name": sheet_summary["sheet_name"],
        "mapping_status": "ok",
        "header_row": header_row,
        "column_mapping": {
            "sequence": num_to_col(mapping["sequence"]),
            "category": num_to_col(mapping["category"]) if mapping.get("category") else None,
            "capability_desc": num_to_col(mapping["capability_desc"]),
            "answer_value": num_to_col(mapping["answer_value"]),
            "proof_material": [num_to_col(col) for col in proof_cols],
        },
        "data_start_row": data_start,
        "data_end_row": data_end,
        "segment_strategy": "one_row_one_segment",
        "confidence": mapping["confidence"],
        "extracted_segment_count": len(segments),
    }
    return segments, mapping_record


def write_visualization(out_dir: Path, segments: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> None:
    data_center_counts = Counter(segment["data_center_id"] for segment in segments)
    file_counts = Counter(segment["relative_path"] for segment in segments)
    attachment_segments = sum(1 for segment in segments if segment["proof_attachment_count"] > 0)
    attachment_total = sum(segment["proof_attachment_count"] for segment in segments)

    lines: list[str] = []
    lines.append("# Step 05 规则化行级 Segment 抽取可视化\n")
    lines.append(f"- 抽取 segment 数：**{len(segments)}**")
    lines.append(f"- 带佐证附件的 segment：**{attachment_segments}**")
    lines.append(f"- 佐证附件记录总数：**{attachment_total}**")
    lines.append("- 本步骤只处理 `knowledge_base` Excel，不处理工勘单。\n")

    lines.append("## 分库统计\n")
    lines.append("| data_center_id | segment_count |")
    lines.append("|---|---:|")
    for data_center_id, count in sorted(data_center_counts.items()):
        lines.append(f"| `{data_center_id}` | {count} |")

    lines.append("\n## 文件统计\n")
    lines.append("| file | segment_count |")
    lines.append("|---|---:|")
    for file_name, count in sorted(file_counts.items()):
        lines.append(f"| `{file_name}` | {count} |")

    lines.append("\n## 表头映射\n")
    lines.append("| file | sheet | header_row | columns | segments | confidence |")
    lines.append("|---|---|---:|---|---:|---:|")
    for mapping in mappings:
        if mapping["mapping_status"] != "ok":
            lines.append(
                f"| `{mapping.get('relative_path','')}` | `{mapping['sheet_name']}` |  | "
                f"`{mapping['mapping_status']}` | 0 | 0 |"
            )
            continue
        cols = ", ".join(f"{k}={v}" for k, v in mapping["column_mapping"].items())
        lines.append(
            f"| `{mapping['relative_path']}` | `{mapping['sheet_name']}` | {mapping['header_row']} | "
            f"`{cols}` | {mapping['extracted_segment_count']} | {mapping['confidence']} |"
        )

    lines.append("\n## Segment 样例\n")
    lines.append("| data_center | file | row | category | capability_desc | answer_value | attachments |")
    lines.append("|---|---|---:|---|---|---|---:|")
    for segment in segments[:20]:
        lines.append(
            f"| `{segment['data_center_id']}` | `{segment['relative_path']}` | {segment['row_index']} | "
            f"`{' / '.join(segment['category_path'])}` | {segment['capability_desc'][:60]} | "
            f"{segment['answer_value'][:60]} | {segment['proof_attachment_count']} |"
        )

    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(structure_dir: Path = DEFAULT_STRUCTURE_DIR, out_dir: Path = DEFAULT_OUT_DIR) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    segments: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []

    for file_record in file_records(structure_dir):
        if file_record.get("document_role") != "knowledge_base":
            continue
        if file_record.get("parser_type") != "xlsx_ooxml" or file_record.get("parse_status") != "ok":
            continue
        for sheet_summary in file_record.get("sheets", []):
            sheet_segments, mapping = extract_from_sheet(structure_dir, file_record, sheet_summary)
            segments.extend(sheet_segments)
            mappings.append(mapping)

    write_jsonl(out_dir / "segments.jsonl", segments)
    write_jsonl(out_dir / "sheet_mappings.jsonl", mappings)

    summary = {
        "total_segments": len(segments),
        "mapping_status_counts": dict(Counter(mapping["mapping_status"] for mapping in mappings)),
        "segment_counts_by_data_center": dict(Counter(segment["data_center_id"] for segment in segments)),
        "segment_counts_by_file": dict(Counter(segment["relative_path"] for segment in segments)),
        "segments_with_attachments": sum(1 for segment in segments if segment["proof_attachment_count"] > 0),
        "total_proof_attachments": sum(segment["proof_attachment_count"] for segment in segments),
    }
    write_json(out_dir / "summary.json", summary)
    write_visualization(out_dir, segments, mappings)
    return segments, mappings


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 05: extract rule-based row-level knowledge-base segments.")
    parser.add_argument("--structure-dir", type=Path, default=DEFAULT_STRUCTURE_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    segments, mappings = run(args.structure_dir, args.out_dir)
    print(f"extracted {len(segments)} segments from {len(mappings)} sheets -> {args.out_dir}")


if __name__ == "__main__":
    main()
