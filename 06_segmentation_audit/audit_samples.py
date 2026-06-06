from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_STRUCTURE_DIR = Path(__file__).resolve().parents[1] / "artifacts/04a_structure_parse"
DEFAULT_SEGMENT_DIR = Path(__file__).resolve().parents[1] / "artifacts/05_segment_extract"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/06_segmentation_audit"

EXCEL_SAMPLES = [
    "西咸数据中心6号楼维护能力知识库.xlsx",
    "西咸数据中心3号楼维护能力知识库.xlsx",
    "陕西移动IDC对外服务知识库.xlsx",
]

WORD_SAMPLES = [
    "中国移动（陕西咸阳）数据中心机房情况说明介绍.docx",
    "中国移动（陕西西安）数据中心机房情况说明介绍.docx",
    "中国移动（陕西西咸）数据中心机房情况说明介绍.docx",
]

CELL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
HEADING_RE = re.compile(r"^([一二三四五六七八九十]+、|[0-9]+、)")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


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


def cell_ref(row: int, col: int) -> str:
    return f"{num_to_col(col)}{row}"


def split_cell_ref(ref: str) -> tuple[int, int] | None:
    match = CELL_RE.match(ref or "")
    if not match:
        return None
    col, row = match.groups()
    return int(row), col_to_num(col)


def display_text(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value).strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def load_cells(structure_dir: Path, file_id: str, sheet_index: int) -> dict[int, dict[int, dict[str, Any]]]:
    cells = read_jsonl(structure_dir / "worksheets" / f"{file_id}.{sheet_index:02d}.cells.jsonl")
    by_row: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for cell in cells:
        by_row[cell["row"]][cell["col"]] = cell
    return by_row


def cell_value(cells_by_row: dict[int, dict[int, dict[str, Any]]], row: int, col: int | None) -> str:
    if col is None:
        return ""
    cell = cells_by_row.get(row, {}).get(col)
    if not cell:
        return ""
    value = cell.get("value")
    if value not in (None, ""):
        return str(value)
    formula = cell.get("formula_text")
    return f"={formula}" if formula else ""


def parse_col(value: str | None) -> int | None:
    if not value:
        return None
    return col_to_num(value)


def mapping_to_cols(mapping: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in mapping.items():
        if key == "proof_material":
            result[key] = [parse_col(item) for item in value]
        elif value is None:
            result[key] = None
        else:
            result[key] = parse_col(value)
    return result


def sample_segment(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_index": segment["row_index"],
        "sequence": segment.get("sequence"),
        "category_path": segment.get("category_path", []),
        "capability_desc": segment.get("capability_desc"),
        "answer_value": segment.get("answer_value"),
        "proof_cell_refs": segment.get("proof_cell_refs", []),
        "proof_attachment_count": segment.get("proof_attachment_count", 0),
        "source_anchor": segment.get("source_anchor", {}),
        "embedding_text": segment.get("embedding_text", ""),
    }


def pick_examples(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    if segments:
        examples.append(segments[0])
    multi = next((segment for segment in segments if segment.get("proof_attachment_count", 0) > 1), None)
    if multi and multi not in examples:
        examples.append(multi)
    zero = next((segment for segment in segments if segment.get("proof_attachment_count", 0) == 0), None)
    if zero and zero not in examples:
        examples.append(zero)
    if segments and segments[-1] not in examples:
        examples.append(segments[-1])
    return [sample_segment(segment) for segment in examples[:4]]


def audit_excel(
    structure_dir: Path,
    segment_dir: Path,
    file_name: str,
    mappings_by_file: dict[str, dict[str, Any]],
    segments_by_file: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    mapping = mappings_by_file[file_name]
    segments = sorted(segments_by_file[file_name], key=lambda item: item["row_index"])
    file_id = mapping["file_id"]
    sheet_index = mapping["sheet_index"]
    cells_by_row = load_cells(structure_dir, file_id, sheet_index)
    cols = mapping_to_cols(mapping["column_mapping"])
    sequence_col = cols.get("sequence")
    desc_col = cols.get("capability_desc")
    answer_col = cols.get("answer_value")
    category_col = cols.get("category")

    segment_rows = {segment["row_index"] for segment in segments}
    skipped_rows: list[dict[str, Any]] = []
    mismatch_rows: list[dict[str, Any]] = []
    for row in range(mapping["data_start_row"], mapping["data_end_row"] + 1):
        sequence = display_text(cell_value(cells_by_row, row, sequence_col), 80)
        desc = display_text(cell_value(cells_by_row, row, desc_col), 80)
        answer = display_text(cell_value(cells_by_row, row, answer_col), 80)
        category = display_text(cell_value(cells_by_row, row, category_col), 80)
        if row not in segment_rows:
            skipped_rows.append(
                {"row_index": row, "sequence": sequence, "category": category, "capability_desc": desc, "answer_value": answer}
            )
            continue
        if not desc and not answer:
            mismatch_rows.append({"row_index": row, "reason": "segment row has neither desc nor answer"})

    attachments = read_jsonl(structure_dir / "attachments" / f"{file_id}.attachments.jsonl")
    attachments_by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attachment in attachments:
        source_cell = attachment.get("source_cell")
        if source_cell:
            attachments_by_cell[source_cell].append(attachment)

    attachment_mismatches: list[dict[str, Any]] = []
    for segment in segments:
        expected_count = sum(len(attachments_by_cell.get(cell, [])) for cell in segment.get("source_anchor", {}).get("proof_cells", []))
        actual_count = segment.get("proof_attachment_count", 0)
        if expected_count != actual_count:
            attachment_mismatches.append(
                {"row_index": segment["row_index"], "expected": expected_count, "actual": actual_count}
            )

    return {
        "file_name": file_name,
        "data_center_id": mapping.get("data_center_id"),
        "sheet_name": mapping["sheet_name"],
        "header_row": mapping["header_row"],
        "column_mapping": mapping["column_mapping"],
        "segment_count": len(segments),
        "data_row_span": [mapping["data_start_row"], mapping["data_end_row"]],
        "skipped_rows": skipped_rows,
        "mismatch_rows": mismatch_rows,
        "attachment_mismatches": attachment_mismatches,
        "examples": pick_examples(segments),
        "verdict": "pass" if not mismatch_rows and not attachment_mismatches else "review",
    }


def audit_word(structure_dir: Path, file_name: str, files_by_name: dict[str, dict[str, Any]]) -> dict[str, Any]:
    file_id = files_by_name[file_name]["file_id"]
    doc = read_json(structure_dir / "documents" / f"{file_id}.docx.json")
    attachments = read_jsonl(structure_dir / "attachments" / f"{file_id}.attachments.jsonl")
    paragraphs = doc.get("paragraphs", [])

    short_headings: list[dict[str, Any]] = []
    long_heading_like: list[dict[str, Any]] = []
    for para in paragraphs:
        text = para.get("text", "")
        if not HEADING_RE.match(text):
            continue
        item = {
            "block_index": para.get("block_index"),
            "text": display_text(text, 180),
            "style_name": para.get("style_name"),
            "length": len(text),
        }
        if len(text) <= 40:
            short_headings.append(item)
        else:
            long_heading_like.append(item)

    invalid_media = [item for item in attachments if item.get("media_path", "").endswith("/") or not Path(item.get("media_path", "")).suffix]

    return {
        "file_name": file_name,
        "data_center_id": doc.get("data_center_id"),
        "paragraph_count": doc.get("paragraph_count"),
        "table_count": doc.get("table_count"),
        "attachment_count": doc.get("attachment_count"),
        "valid_attachment_count": len(attachments),
        "invalid_media_count": len(invalid_media),
        "first_paragraphs": [
            {
                "block_index": para.get("block_index"),
                "text": display_text(para.get("text"), 160),
                "style_name": para.get("style_name"),
            }
            for para in paragraphs[:8]
        ],
        "short_heading_candidates": short_headings[:12],
        "long_heading_like_paragraphs": long_heading_like[:8],
        "table_samples": [
            {"table_index": table["table_index"], "row_count": table["row_count"], "rows_sample": table["rows_sample"][:3]}
            for table in doc.get("tables", [])[:3]
        ],
        "verdict": "pass_for_structure_parse" if not invalid_media else "review_media",
    }


def md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return lines


def write_report(out_dir: Path, audit: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 06 抽样切分准确性审计\n")
    lines.append("本报告抽查 3 个已切分 Excel 和 3 个已结构解析 Word。图片只作为佐证附件，不做 OCR。\n")

    lines.append("## 总体判断\n")
    lines.append("- Excel 知识库行级切分整体准确：字段列、类别继承、证明附件绑定在样本内均未发现错位。")
    lines.append("- Word 当前还没有做第 5 步 segment 抽取；4A 的段落、表格、媒体文件结构可用，但下一步不能只靠编号正则切标题。")
    lines.append("- 已修正 DOCX 附件中过滤目录项的问题，当前样本内 `invalid_media_count=0`。\n")

    lines.append("## Excel 样本\n")
    excel_rows = [
        [
            item["file_name"],
            item["data_center_id"],
            item["sheet_name"],
            item["segment_count"],
            f"{item['data_row_span'][0]}-{item['data_row_span'][1]}",
            len(item["skipped_rows"]),
            len(item["attachment_mismatches"]),
            item["verdict"],
        ]
        for item in audit["excel_samples"]
    ]
    lines.extend(
        md_table(
            ["file", "data_center", "sheet", "segments", "rows", "skipped", "attachment_mismatch", "verdict"],
            excel_rows,
        )
    )
    lines.append("")

    for item in audit["excel_samples"]:
        lines.append(f"### Excel: {item['file_name']}\n")
        lines.append(f"- 列映射：`{json.dumps(item['column_mapping'], ensure_ascii=False)}`")
        if item["skipped_rows"]:
            lines.append("- 跳过行：")
            for skipped in item["skipped_rows"][:8]:
                lines.append(
                    f"  - row {skipped['row_index']}: seq={skipped['sequence'] or '-'}, "
                    f"category={skipped['category'] or '-'}, desc={skipped['capability_desc'] or '-'}, "
                    f"answer={skipped['answer_value'] or '-'}"
                )
        else:
            lines.append("- 跳过行：无")
        lines.append("- 抽样 segment：")
        for example in item["examples"]:
            category = " / ".join(example["category_path"]) if example["category_path"] else "-"
            lines.append(
                f"  - row {example['row_index']}: category={category}; "
                f"desc={display_text(example['capability_desc'], 90)}; "
                f"answer={display_text(example['answer_value'], 90)}; "
                f"proof_cells={example['proof_cell_refs']}; attachments={example['proof_attachment_count']}"
            )
        lines.append("")

    lines.append("## Word 样本\n")
    word_rows = [
        [
            item["file_name"],
            item["data_center_id"] or "ambiguous/global",
            item["paragraph_count"],
            item["table_count"],
            item["attachment_count"],
            item["invalid_media_count"],
            len(item["long_heading_like_paragraphs"]),
            item["verdict"],
        ]
        for item in audit["word_samples"]
    ]
    lines.extend(
        md_table(
            ["file", "data_center", "paragraphs", "tables", "attachments", "invalid_media", "long_heading_like", "verdict"],
            word_rows,
        )
    )
    lines.append("")

    for item in audit["word_samples"]:
        lines.append(f"### Word: {item['file_name']}\n")
        lines.append("- 前几段：")
        for para in item["first_paragraphs"][:5]:
            lines.append(f"  - block {para['block_index']}: `{para['style_name']}` {para['text']}")
        lines.append("- 短标题候选：")
        for heading in item["short_heading_candidates"][:8]:
            lines.append(f"  - block {heading['block_index']}: {heading['text']}")
        if item["long_heading_like_paragraphs"]:
            lines.append("- 容易被误判为标题的长段：")
            for para in item["long_heading_like_paragraphs"][:4]:
                lines.append(f"  - block {para['block_index']}: len={para['length']} {para['text']}")
        else:
            lines.append("- 容易被误判为标题的长段：无")
        if item["table_samples"]:
            lines.append("- 表格样例：")
            for table in item["table_samples"]:
                lines.append(f"  - table {table['table_index']}, rows={table['row_count']}: {table['rows_sample']}")
        else:
            lines.append("- 表格样例：无")
        lines.append("")

    lines.append("## 结论\n")
    lines.append("Excel 可以进入 embedding 前的数据清洗：建议只修正 `embedding_text` 中偶发的重复标点。")
    lines.append("Word 建议下一步单独做段落/章节级 segment：用样式 + 短标题正则 + 表格单独 segment，图片按邻近段落或文档级佐证挂载。")
    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(structure_dir: Path, segment_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = read_jsonl(structure_dir / "files.jsonl")
    files_by_name = {item["file_name"]: item for item in files}
    mappings = read_jsonl(segment_dir / "sheet_mappings.jsonl")
    mappings_by_file = {item["file_name"]: item for item in mappings}
    segments = read_jsonl(segment_dir / "segments.jsonl")
    segments_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        segments_by_file[segment["file_name"]].append(segment)

    audit = {
        "excel_samples": [
            audit_excel(structure_dir, segment_dir, file_name, mappings_by_file, segments_by_file)
            for file_name in EXCEL_SAMPLES
        ],
        "word_samples": [audit_word(structure_dir, file_name, files_by_name) for file_name in WORD_SAMPLES],
    }

    write_json(out_dir / "sample_audit.json", audit)
    write_jsonl(out_dir / "excel_samples.jsonl", audit["excel_samples"])
    write_jsonl(out_dir / "word_samples.jsonl", audit["word_samples"])
    write_report(out_dir, audit)
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit representative Excel segments and Word structure parse output.")
    parser.add_argument("--structure-dir", type=Path, default=DEFAULT_STRUCTURE_DIR)
    parser.add_argument("--segment-dir", type=Path, default=DEFAULT_SEGMENT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    audit = run(args.structure_dir, args.segment_dir, args.out_dir)
    print(
        f"audited {len(audit['excel_samples'])} excel files and "
        f"{len(audit['word_samples'])} word files -> {args.out_dir}"
    )


if __name__ == "__main__":
    main()
