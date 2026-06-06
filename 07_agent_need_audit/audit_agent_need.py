from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_STRUCTURE_DIR = Path(__file__).resolve().parents[1] / "artifacts/04a_structure_parse"
DEFAULT_EMBEDDED_DIR = Path(__file__).resolve().parents[1] / "artifacts/04b_embedded_object_parse"
DEFAULT_SEGMENT_DIR = Path(__file__).resolve().parents[1] / "artifacts/05_segment_extract"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/07_agent_need_audit"

HEADER_KEYWORDS = {
    "序号",
    "类别",
    "项目",
    "名称",
    "指标",
    "能力描述",
    "是否满足",
    "证明材料",
    "结果",
    "异常描述",
    "业务链路",
    "接入设备",
    "接入端口",
    "实际带宽",
    "ODF信息",
    "设备型号",
    "设备名称",
    "设备位置",
    "设备IP",
    "机房名称",
    "聚合组",
    "聚合组带宽(G)",
    "机房出口总带宽(G)",
    "对端节点",
    "带宽",
    "已使用",
    "自有业务",
    "第三方业务",
    "IDC大机柜Top5客户",
    "IDC大带宽Top5客户",
    "巡检时间",
    "巡检人",
}


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


def display_text(value: Any, limit: int = 160) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value).strip())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def header_score(values: list[str]) -> int:
    score = 0
    for value in values:
        cleaned = display_text(value, 80)
        if cleaned in HEADER_KEYWORDS:
            score += 2
        elif any(keyword in cleaned for keyword in HEADER_KEYWORDS if len(keyword) >= 2):
            score += 1
    return score


def value_like_header(values: list[str]) -> bool:
    if not values:
        return False
    value_like = 0
    for value in values:
        text = display_text(value, 80)
        if re.search(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", text):
            value_like += 1
        elif re.search(r"\d+(\.\d+)?\s*[GMK]?$", text, re.I):
            value_like += 1
        elif len(text) > 30:
            value_like += 1
    return value_like >= max(1, len(values) // 2)


def add_case(
    cases: list[dict[str, Any]],
    need_type: str,
    severity: str,
    category: str,
    file_name: str,
    anchor: str,
    reason: str,
    suggested_action: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    cases.append(
        {
            "need_type": need_type,
            "severity": severity,
            "category": category,
            "file_name": file_name,
            "anchor": anchor,
            "reason": reason,
            "suggested_action": suggested_action,
            "evidence": evidence or {},
        }
    )


def full_table_rows(table: dict[str, Any]) -> list[list[str]]:
    by_row: dict[int, dict[int, str]] = defaultdict(dict)
    max_col = 0
    for cell in table.get("cells", []):
        row_index = int(cell["row_index"])
        col_index = int(cell["col_index"])
        by_row[row_index][col_index] = display_text(cell.get("text"))
        max_col = max(max_col, col_index)
    rows: list[list[str]] = []
    for row_index in sorted(by_row):
        row = [by_row[row_index].get(col, "") for col in range(1, max_col + 1)]
        while row and not row[-1]:
            row.pop()
        rows.append(row)
    return rows


def audit_main_excel_mappings(segment_dir: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    mappings = read_jsonl(segment_dir / "sheet_mappings.jsonl")
    rule_ok = 0
    for mapping in mappings:
        confidence = float(mapping.get("confidence") or 0)
        cols = mapping.get("column_mapping", {})
        if mapping.get("mapping_status") == "ok" and confidence >= 0.8 and cols.get("capability_desc") and cols.get("answer_value"):
            rule_ok += 1
            continue
        add_case(
            cases,
            "agent_candidate",
            "medium",
            "main_excel_table_mapping",
            mapping.get("file_name", ""),
            mapping.get("sheet_name", ""),
            "主知识库表头映射置信度低或关键列缺失。",
            "先让规则给候选表头；仅对该表调用 LLM 判断 header_row、data_start_row、字段列映射。",
            {"confidence": confidence, "column_mapping": cols},
        )
    return {"total": len(mappings), "rule_ok": rule_ok, "agent_candidate": len(mappings) - rule_ok}


def audit_intro_doc_tables(structure_dir: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    stats = Counter()
    for doc_path in sorted((structure_dir / "documents").glob("*.docx.json")):
        doc = read_json(doc_path)
        if doc.get("document_role") != "intro_doc":
            continue
        for table in doc.get("tables", []):
            stats["total"] += 1
            rows = full_table_rows(table)
            candidates = rows[:3]
            best_score = max((header_score(row) for row in candidates), default=0)
            best_row = max(candidates, key=header_score, default=[])
            anchor = f"table {table.get('table_index')}"
            if table.get("row_count", 0) <= 2:
                stats["rule_ok"] += 1
                continue
            if best_score <= 0 or value_like_header(best_row):
                stats["agent_candidate"] += 1
                add_case(
                    cases,
                    "agent_candidate",
                    "medium",
                    "intro_doc_table_structure",
                    doc.get("file_name", ""),
                    anchor,
                    "Word 介绍文档表格的表头不明显，规则无法高置信区分元数据行、表头行和数据行。",
                    "LLM 只判断 table_type、context_rows、header_rows、data_start_row；代码再据此切分。",
                    {"rows_sample": rows[:5], "best_header_score": best_score},
                )
            else:
                stats["rule_ok"] += 1
    return dict(stats)


def audit_embedded_objects(embedded_dir: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    objects = read_jsonl(embedded_dir / "embedded_objects.jsonl")
    stats = Counter(total=len(objects))
    for item in objects:
        parse_status = item.get("parse_status")
        file_type = item.get("embedded_file_type")
        file_name = item.get("parent_file_name") or ""
        anchor = f"{item.get('parent_sheet_name') or ''}!{item.get('parent_source_cell') or 'unmapped'}"
        if not item.get("parent_source_cell"):
            stats["deterministic_backlog"] += 1
            add_case(
                cases,
                "deterministic_backlog",
                "high",
                "embedded_object_parent_mapping",
                file_name,
                anchor,
                "嵌入对象已抽出，但没有映射到具体证明材料单元格；不能可靠继承父行标签。",
                "优先补 VML/OLE anchor 映射规则；这不是 LLM 问题。",
                {
                    "embedded_file_name": item.get("embedded_file_name"),
                    "embedded_file_type": file_type,
                    "parse_status": parse_status,
                },
            )
            continue
        if parse_status in {"parsed", "parsed_archive"}:
            stats["rule_ok"] += 1
        elif file_type in {"rar", "pdf", "unknown", "dwg", "doc", "vsdx", "pptx"} or parse_status in {
            "extracted_only",
            "listed_archive",
            "unsupported_archive",
            "error",
        }:
            stats["deterministic_backlog"] += 1
            action = {
                "rar": "使用支持 RAR5 的解压器；如果内部仍是图片，按图片佐证处理，不 OCR。",
                "pdf": "补 PDF 文本解析器；如果是扫描图，按图片佐证处理，不 OCR。",
                "doc": "用 textutil/LibreOffice/antiword 转出旧版 Word 文本后再切分。",
                "dwg": "DWG 图纸需 CAD 转换器；若无法文本化，作为图纸佐证资产保留。",
                "vsdx": "解析 Visio XML 文本；没有文本时作为拓扑佐证资产保留。",
                "pptx": "解析幻灯片文本；没有文本时作为演示佐证资产保留。",
                "unknown": "根据真实扩展名补转换器，例如 DWG/CAD 或旧版 Office 转 OOXML。",
            }.get(file_type, "补确定性解析器。")
            add_case(
                cases,
                "deterministic_backlog",
                "medium",
                "embedded_object_unparsed",
                file_name,
                anchor,
                f"嵌入对象类型为 {file_type}，当前状态为 {parse_status}，未生成可向量化子块。",
                action,
                {
                    "embedded_file_name": item.get("embedded_file_name"),
                    "child_file_count": item.get("child_file_count"),
                    "child_segment_count": item.get("child_segment_count"),
                },
            )
        else:
            stats["rule_ok"] += 1
    return dict(stats)


def audit_embedded_tables(embedded_dir: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    segments = read_jsonl(embedded_dir / "embedded_segments.jsonl")
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        anchor = segment.get("local_anchor", {})
        if segment.get("segment_type") != "embedded_docx_table_row":
            continue
        table_index = anchor.get("table_index")
        if table_index is None:
            continue
        groups[
            (
                segment.get("parent_attachment_id", ""),
                segment.get("embedded_file_name", ""),
                int(table_index),
            )
        ].append(segment)

    stats = Counter(total=len(groups))
    for (_, _, table_index), rows in groups.items():
        rows = sorted(rows, key=lambda item: item.get("local_anchor", {}).get("row_index", 0))
        first = rows[0]
        file_name = first.get("parent_file_name", "")
        parent_anchor = f"{first.get('parent_sheet_name')}!{first.get('parent_source_cell')} table {table_index}"
        headers = [row.get("local_anchor", {}).get("table_header") or [] for row in rows]
        has_header = any(headers)
        weak_header = any(header and header_score(header) <= 0 and len(header) >= 2 for header in headers)
        missing_section = any(not row.get("local_anchor", {}).get("section_context") for row in rows)
        placeholder = any(re.search(r"列\d+：", row.get("raw_text", "")) for row in rows)
        if not has_header or weak_header or missing_section or placeholder:
            stats["agent_candidate"] += 1
            add_case(
                cases,
                "agent_candidate",
                "low",
                "embedded_word_table_structure",
                file_name,
                parent_anchor,
                "嵌入 Word 表格的章节/表头上下文仍有低置信信号。",
                "仅对该表调用 LLM 判断 context_rows、header_rows、data_rows；输出结构 JSON 后由代码重切。",
                {
                    "rows_sample": [row.get("raw_text") for row in rows[:5]],
                    "headers_sample": headers[:5],
                    "missing_section": missing_section,
                    "placeholder": placeholder,
                    "parent_attachment_id": first.get("parent_attachment_id"),
                    "embedded_file_name": first.get("embedded_file_name"),
                    "embedded_file_type": first.get("embedded_file_type"),
                },
            )
        else:
            stats["rule_ok"] += 1
    return dict(stats)


def write_visualization(out_dir: Path, summary: dict[str, Any], cases: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# Step 07 智能体介入点全量审计\n")
    lines.append("本报告扫描主知识库 Excel、介绍 Word、04B 嵌入对象和嵌入 Word 表格。结论分三类：规则足够、确定性解析待补、智能体候选。\n")

    lines.append("## 总览\n")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    for key, value in summary.items():
        if isinstance(value, (str, int, float)):
            lines.append(f"| `{key}` | {value} |")

    lines.append("\n## 案例类型统计\n")
    lines.append("| need_type | count |")
    lines.append("|---|---:|")
    for key, value in sorted(Counter(case["need_type"] for case in cases).items()):
        lines.append(f"| `{key}` | {value} |")

    lines.append("\n## 结论\n")
    lines.append("- 主知识库 Excel 表头稳定，当前不需要智能体参与。")
    lines.append("- 已能解析的嵌入 Word/Excel 子表，优先用规则：章节上下文 + 表头键值化。")
    lines.append("- 真正适合 LLM/智能体兜底的是低置信表格结构判定；当前全量样本里数量很少。")
    lines.append("- 大量问题其实是确定性解析待补：未映射 OLE 父单元格、RAR5、PDF、DWG、旧版 Office，而不是智能体问题。\n")

    lines.append("## 高优先级案例\n")
    lines.append("| type | severity | category | file | anchor | reason | action |")
    lines.append("|---|---|---|---|---|---|---|")
    priority = {"high": 0, "medium": 1, "low": 2}
    for case in sorted(cases, key=lambda item: (priority.get(item["severity"], 9), item["need_type"], item["category"]))[:80]:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{case['need_type']}`",
                    f"`{case['severity']}`",
                    f"`{case['category']}`",
                    f"`{case['file_name']}`",
                    f"`{case['anchor']}`",
                    display_text(case["reason"], 120),
                    display_text(case["suggested_action"], 140),
                ]
            )
            + " |"
        )
    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def markdown_cell(value: Any, limit: int = 120) -> str:
    text = display_text(value, limit)
    return text.replace("|", "\\|")


def embedded_object_need(item: dict[str, Any]) -> tuple[str, str, str]:
    parse_status = item.get("parse_status")
    file_type = item.get("embedded_file_type")
    if not item.get("parent_source_cell"):
        return ("deterministic_backlog", "high", "未映射父单元格")
    if parse_status in {"parsed", "parsed_archive"}:
        return ("rule_ok", "ok", "已解析并能继承父位置")
    if file_type in {"rar", "pdf", "unknown", "dwg", "doc", "vsdx", "pptx"}:
        return ("deterministic_backlog", "medium", f"{file_type} 未生成文本子块")
    return ("rule_ok", "ok", "暂按佐证附件处理")


def embedded_table_need(rows: list[dict[str, Any]]) -> tuple[str, str, list[str], list[str], str]:
    rows = sorted(rows, key=lambda item: item.get("local_anchor", {}).get("row_index", 0))
    headers = [row.get("local_anchor", {}).get("table_header") or [] for row in rows]
    has_header = any(headers)
    weak_header = any(header and header_score(header) <= 0 and len(header) >= 2 for header in headers)
    missing_section = any(not row.get("local_anchor", {}).get("section_context") for row in rows)
    placeholder = any(re.search(r"列\d+：", row.get("raw_text", "")) for row in rows)
    flags: list[str] = []
    if not has_header:
        flags.append("无表头")
    if weak_header:
        flags.append("弱表头")
    if missing_section:
        flags.append("缺章节")
    if placeholder:
        flags.append("占位列名")
    status = "agent_candidate" if flags else "rule_ok"
    section = rows[0].get("local_anchor", {}).get("section_context") or []
    header = next((header for header in headers if header), [])
    sample = rows[0].get("raw_text", "") if rows else ""
    return (status, "、".join(flags) if flags else "章节和表头可规则化", section, header, sample)


def write_details(out_dir: Path, embedded_dir: Path) -> None:
    objects = read_jsonl(embedded_dir / "embedded_objects.jsonl")
    segments = read_jsonl(embedded_dir / "embedded_segments.jsonl")
    object_by_attachment = {item.get("parent_attachment_id"): item for item in objects}

    table_groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        anchor = segment.get("local_anchor", {})
        if segment.get("segment_type") != "embedded_docx_table_row":
            continue
        table_index = anchor.get("table_index")
        if table_index is None:
            continue
        table_groups[
            (
                segment.get("parent_attachment_id", ""),
                segment.get("embedded_file_name", ""),
                int(table_index),
            )
        ].append(segment)

    lines: list[str] = []
    lines.append("# Step 07 明细：嵌入对象与嵌入 Word 表格\n")
    lines.append(f"这份明细回答两个问题：{len(objects)} 个嵌入对象分别在哪里；{len(table_groups)} 张嵌入 Word 表格里哪些规则可处理、哪些建议走智能体结构兜底。\n")

    lines.append(f"## {len(objects)} 个嵌入对象\n")
    lines.append("| # | 判定 | 严重度 | 数据中心 | 父文件 | sheet!cell | 对象 | 类型 | 状态 | 子文件 | 子块 | 说明 |")
    lines.append("|---:|---|---|---|---|---|---|---|---|---:|---:|---|")
    sorted_objects = sorted(
        objects,
        key=lambda item: (
            item.get("parent_file_name") or "",
            item.get("parent_sheet_name") or "",
            item.get("parent_source_cell") or "unmapped",
            item.get("object_shape_id") or "",
        ),
    )
    for index, item in enumerate(sorted_objects, 1):
        need_type, severity, note = embedded_object_need(item)
        cell = item.get("parent_source_cell") or "unmapped"
        anchor = f"{item.get('parent_sheet_name') or ''}!{cell}"
        obj_name = item.get("embedded_file_name") or item.get("prog_id") or item.get("parent_attachment_id")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{need_type}`",
                    f"`{severity}`",
                    f"`{markdown_cell(item.get('data_center_id'), 40)}`",
                    f"`{markdown_cell(item.get('parent_file_name'), 90)}`",
                    f"`{markdown_cell(anchor, 60)}`",
                    markdown_cell(obj_name, 90),
                    f"`{markdown_cell(item.get('embedded_file_type'), 30)}`",
                    f"`{markdown_cell(item.get('parse_status'), 30)}`",
                    str(item.get("child_file_count") or 0),
                    str(item.get("child_segment_count") or 0),
                    markdown_cell(note, 80),
                ]
            )
            + " |"
        )

    lines.append(f"\n## {len(table_groups)} 张嵌入 Word 表格\n")
    lines.append("| # | 判定 | 父文件 | sheet!cell | 附件ID | 嵌入文件 | 表 | 行数 | 触发原因 | 章节样例 | 表头样例 | 行样例 |")
    lines.append("|---:|---|---|---|---|---|---:|---:|---|---|---|---|")
    sorted_tables = sorted(
        table_groups.items(),
        key=lambda item: (
            item[1][0].get("parent_file_name") if item[1] else "",
            item[0][0],
            item[0][1],
            item[0][2],
        ),
    )
    for index, ((attachment_id, embedded_file_name, table_index), rows) in enumerate(sorted_tables, 1):
        rows = sorted(rows, key=lambda item: item.get("local_anchor", {}).get("row_index", 0))
        first = rows[0]
        obj = object_by_attachment.get(attachment_id, {})
        status, reason, section, header, sample = embedded_table_need(rows)
        parent_cell = first.get("parent_source_cell") or obj.get("parent_source_cell") or "unmapped"
        sheet = first.get("parent_sheet_name") or obj.get("parent_sheet_name") or ""
        anchor = f"{sheet}!{parent_cell}"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{status}`",
                    f"`{markdown_cell(first.get('parent_file_name'), 90)}`",
                    f"`{markdown_cell(anchor, 60)}`",
                    f"`{markdown_cell(attachment_id, 50)}`",
                    markdown_cell(embedded_file_name, 80),
                    str(table_index),
                    str(len(rows)),
                    markdown_cell(reason, 80),
                    markdown_cell(" > ".join(section), 100),
                    markdown_cell(" | ".join(header), 100),
                    markdown_cell(sample, 120),
                ]
            )
            + " |"
        )

    (out_dir / "details.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(structure_dir: Path, embedded_dir: Path, segment_dir: Path, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    main_excel = audit_main_excel_mappings(segment_dir, cases)
    intro_tables = audit_intro_doc_tables(structure_dir, cases)
    embedded_objects = audit_embedded_objects(embedded_dir, cases)
    embedded_tables = audit_embedded_tables(embedded_dir, cases)

    case_counts = Counter(case["need_type"] for case in cases)
    summary = {
        "main_excel_tables": main_excel,
        "intro_doc_tables": intro_tables,
        "embedded_objects": embedded_objects,
        "embedded_word_tables": embedded_tables,
        "agent_candidate_cases": case_counts.get("agent_candidate", 0),
        "deterministic_backlog_cases": case_counts.get("deterministic_backlog", 0),
        "total_cases": len(cases),
    }
    write_json(out_dir / "agent_need_summary.json", summary)
    write_jsonl(out_dir / "agent_need_cases.jsonl", cases)
    write_visualization(out_dir, summary, cases)
    write_details(out_dir, embedded_dir)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit where agent/LLM fallback may be needed in knowledge files.")
    parser.add_argument("--structure-dir", type=Path, default=DEFAULT_STRUCTURE_DIR)
    parser.add_argument("--embedded-dir", type=Path, default=DEFAULT_EMBEDDED_DIR)
    parser.add_argument("--segment-dir", type=Path, default=DEFAULT_SEGMENT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    summary = run(args.structure_dir, args.embedded_dir, args.segment_dir, args.out_dir)
    print(
        f"audited agent need: {summary['agent_candidate_cases']} agent candidates, "
        f"{summary['deterministic_backlog_cases']} deterministic backlog cases -> {args.out_dir}"
    )


if __name__ == "__main__":
    main()
