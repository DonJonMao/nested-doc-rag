from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_CLASSIFICATION = Path("/Users/mao/projects/datacenter/artifacts/07_agent_need_audit/table_candidate_classification.json")
DEFAULT_EMBEDDED_SEGMENTS = Path("/Users/mao/projects/datacenter/artifacts/04b_embedded_object_parse/embedded_segments.jsonl")
DEFAULT_HINTS = Path("/Users/mao/projects/datacenter/artifacts/08_llm_structure_hint/table_structure_hints.jsonl")
DEFAULT_OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/09_table_candidate_resolution")

LLM_HINT_CATEGORIES = {
    "绩效考核-年度考核明细",
    "服务报告-IP地址段",
    "服务报告-满意度评价",
}

BLANK_MARKERS = {"", "\\", "/", "／", "-", "—", "－"}


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


def display_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if limit and len(text) > limit:
        return text[: limit - 1] + "..."
    return text


def is_blank(value: Any) -> bool:
    return display_text(value) in BLANK_MARKERS


def clean_header(value: Any) -> str:
    text = display_text(value)
    text = re.sub(r"\s+", "", text)
    text = text.rstrip("：:")
    return text


def compact_semantic_values(values: list[str]) -> list[str]:
    compacted: list[str] = []
    for value in values:
        text = display_text(value)
        if not text:
            continue
        if compacted and compacted[-1] == text:
            continue
        compacted.append(text)
    return compacted


def compare_text(value: Any) -> str:
    return re.sub(r"\s+", "", display_text(value))


def parse_anchor(anchor: str) -> tuple[str, str, int | None]:
    match = re.match(r"(.+)!([A-Z]+\d+)\s+table\s+(\d+)", anchor)
    if not match:
        return "", "", None
    return match.group(1), match.group(2), int(match.group(3))


def candidate_key(record: dict[str, Any]) -> tuple[str, str, str, int, str, str]:
    sheet_name, source_cell, table_no = parse_anchor(record.get("anchor", ""))
    if record.get("parent_attachment_id") or record.get("embedded_file_name"):
        return (
            record.get("file_name", ""),
            sheet_name,
            source_cell,
            int(table_no or -1),
            record.get("parent_attachment_id") or "",
            record.get("embedded_file_name") or "",
        )
    return (record.get("file_name", ""), sheet_name, source_cell, int(table_no or -1), "", "")


def group_embedded_table_rows(segments_path: Path) -> dict[tuple[str, str, str, int, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for segment in read_jsonl(segments_path):
        anchor = segment.get("local_anchor", {})
        if segment.get("segment_type") != "embedded_docx_table_row":
            continue
        table_index = anchor.get("table_index")
        if table_index is None:
            continue
        row = dict(segment)
        row["row_index"] = int(anchor.get("row_index") or 0)
        row["row_values"] = [display_text(item) for item in anchor.get("row_values") or []]
        row["section_context"] = [display_text(item) for item in anchor.get("section_context") or [] if display_text(item)]
        grouped[
            (
                segment.get("parent_file_name", ""),
                segment.get("parent_sheet_name", ""),
                segment.get("parent_source_cell", ""),
                int(table_index),
                segment.get("parent_attachment_id", ""),
                segment.get("embedded_file_name", ""),
            )
        ].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda item: item["row_index"])
    return grouped


def load_hints(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    hints: dict[tuple[str, str], dict[str, Any]] = {}
    for record in read_jsonl(path):
        if record.get("validation", {}).get("status") != "valid":
            continue
        hints[(record.get("file_name", ""), record.get("anchor", ""))] = record
    return hints


def row_map(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["row_index"]): row for row in rows}


def row_values(rows_by_index: dict[int, dict[str, Any]], row_index: int) -> list[str]:
    return [display_text(item) for item in rows_by_index.get(row_index, {}).get("row_values") or []]


def pair_values(headers: list[str], values: list[str], *, fallback_prefix: str = "字段") -> tuple[str, list[str]]:
    parts: list[str] = []
    fallback_headers: list[str] = []
    for index, value in enumerate(values):
        if is_blank(value):
            continue
        generated_fallback = False
        key = clean_header(headers[index]) if index < len(headers) and clean_header(headers[index]) else ""
        if not key:
            key = f"{fallback_prefix}{index + 1}"
            generated_fallback = True
        if generated_fallback:
            fallback_headers.append(key)
        parts.append(f"{key}：{display_text(value)}")
    return "；".join(parts), fallback_headers


def alternating_pairs(values: list[str]) -> tuple[str, list[str]]:
    parts: list[str] = []
    fallback_headers: list[str] = []
    for index in range(0, len(values), 2):
        key = clean_header(values[index]) if index < len(values) else ""
        value = values[index + 1] if index + 1 < len(values) else ""
        if not key:
            key = f"字段{index + 1}"
            fallback_headers.append(key)
        if is_blank(value):
            continue
        parts.append(f"{key}：{display_text(value)}")
    return "；".join(parts), fallback_headers


def context_from_rows(rows: list[dict[str, Any]], fallback: str) -> str:
    for row in rows:
        context = row.get("section_context") or []
        if context:
            return " / ".join(context)
    if fallback.startswith("岗位职责"):
        return "岗位职责"
    if fallback.startswith("绩效考核"):
        return "绩效考核"
    if fallback.startswith("服务报告"):
        return "服务报告"
    if fallback.startswith("应急预案"):
        return "应急预案"
    return fallback


def stable_id(*parts: Any) -> str:
    text = "|".join(display_text(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def table_identity(record: dict[str, Any], row: dict[str, Any] | None = None) -> str:
    row = row or {}
    return stable_id(
        record.get("file_name"),
        record.get("anchor"),
        record.get("parent_attachment_id") or row.get("parent_attachment_id"),
        record.get("embedded_file_name") or row.get("embedded_file_name"),
    )


def table_subject_from_row(row: dict[str, Any] | None) -> str:
    values = compact_semantic_values((row or {}).get("row_values") or [])
    raw, _ = alternating_pairs(values)
    return raw


def with_subject(subject: str, raw: str) -> str:
    subject = display_text(subject)
    raw = display_text(raw)
    if not subject or not raw or raw.startswith(subject):
        return raw
    return f"{subject}；{raw}"


def base_table_record(record: dict[str, Any], rows: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    first = rows[0] if rows else {}
    return {
        "table_id": f"tblres_{table_identity(record, first)}",
        "source_group": record.get("source_group"),
        "table_category": record.get("table_category"),
        "file_name": record.get("file_name"),
        "anchor": record.get("anchor"),
        "table_no": record.get("table_no"),
        "data_center_id": first.get("data_center_id"),
        "parent_file_name": first.get("parent_file_name"),
        "parent_sheet_name": first.get("parent_sheet_name"),
        "parent_source_cell": first.get("parent_source_cell"),
        "parent_attachment_id": first.get("parent_attachment_id"),
        "embedded_file_name": first.get("embedded_file_name"),
        "embedded_file_type": first.get("embedded_file_type"),
        "row_count": len(rows),
        "strategy": strategy,
    }


def make_segment(
    record: dict[str, Any],
    rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    *,
    strategy: str,
    segment_role: str,
    headers: list[str],
    values: list[str],
    raw_text: str,
    context: str,
    group: str = "",
    llm_hint_id: str | None = None,
    fallback_headers: list[str] | None = None,
    table_subject: str = "",
) -> dict[str, Any]:
    first = source_rows[0] if source_rows else (rows[0] if rows else {})
    source_row_indices = [int(row.get("row_index") or 0) for row in source_rows]
    table_id = f"tblres_{table_identity(record, first)}"
    parts = [
        f"数据中心：{first.get('data_center_id')}。" if first.get("data_center_id") else "",
        f"父文件：{record.get('file_name')}。",
        f"父位置：{record.get('anchor')}。",
        f"嵌入文件：{first.get('embedded_file_name')}。" if first.get("embedded_file_name") else "",
        f"表格类型：{record.get('table_category')}。",
        f"表格主题：{table_subject}。" if table_subject else "",
        f"上下文：{context}。" if context else "",
        f"分组：{group}。" if group else "",
        f"内容：{raw_text}。",
    ]
    return {
        "segment_id": f"tblseg_{stable_id(record.get('file_name'), record.get('anchor'), segment_role, source_row_indices, raw_text)}",
        "segment_type": "resolved_embedded_word_table",
        "segment_role": segment_role,
        "table_id": table_id,
        "source_group": record.get("source_group"),
        "table_category": record.get("table_category"),
        "file_name": record.get("file_name"),
        "anchor": record.get("anchor"),
        "table_no": record.get("table_no"),
        "data_center_id": first.get("data_center_id"),
        "parent_file_name": first.get("parent_file_name"),
        "parent_sheet_name": first.get("parent_sheet_name"),
        "parent_source_cell": first.get("parent_source_cell"),
        "parent_segment_id": first.get("parent_segment_id"),
        "parent_attachment_id": first.get("parent_attachment_id"),
        "embedded_file_name": first.get("embedded_file_name"),
        "embedded_file_type": first.get("embedded_file_type"),
        "source_row_indices": source_row_indices,
        "column_headers": headers,
        "row_values": values,
        "group": group,
        "context": context,
        "table_subject": table_subject,
        "raw_text": raw_text,
        "embedding_text": "".join(parts),
        "resolution_policy": strategy,
        "llm_hint_id": llm_hint_id,
        "quality_flags": {
            "has_placeholder_column_label": bool(re.search(r"列\d+：", raw_text)),
            "fallback_headers": fallback_headers or [],
            "empty_raw_text": not bool(display_text(raw_text)),
        },
        "source_policy": "content_from_original_rows; structure_from_rule_or_llm_hint",
    }


class Resolver:
    def __init__(self, hints: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.hints = hints

    def resolve(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        category = record.get("table_category", "")
        if category == "岗位职责-部门职责总览":
            return self.resolve_department(record, rows)
        if category == "岗位职责-岗位说明书":
            return self.resolve_position(record, rows)
        if category == "绩效考核-评分档位":
            return self.resolve_score_grade(record, rows)
        if category == "服务报告-资源机柜分布":
            return self.resolve_cabinet_distribution(record, rows)
        if category == "服务报告-温湿度监控":
            return self.resolve_temperature_humidity(record, rows)
        if category == "服务报告-满意度评价":
            return self.resolve_questionnaire(record, rows)
        if category == "服务报告-重大事件总结":
            return self.resolve_title_header_table(record, rows, ["序号", "重大事件", "影响性分析", "原因分析", "改进建议和方案"])
        if category == "服务报告-未达标总结":
            return self.resolve_title_header_table(record, rows, ["序号", "未达标情况", "影响性分析", "原因分析", "改进建议和方案"])
        if category == "绩效考核-年度考核明细":
            return self.resolve_performance_detail(record, rows)
        if category == "服务报告-IP地址段":
            return self.resolve_ip_segments(record, rows)
        if category == "应急预案-保障队伍组织":
            return self.resolve_org_team(record, rows)
        if category == "应急预案-网络设备清单":
            return self.resolve_network_devices(record, rows)
        if category == "应急预案-零信任登录4A步骤":
            return self.resolve_step_instruction(record, rows)
        if category == "应急预案-应急处理原则":
            return self.resolve_single_statement(record, rows)
        if category == "应急预案-响应角色职责":
            return self.resolve_response_roles(record, rows)
        if category == "应急预案-链路故障操作示例":
            return self.resolve_command_example(record, rows)
        if category == "应急预案-设备故障排查命令":
            return self.resolve_fault_command(record, rows)
        if category == "应急预案-安全事件上报时限":
            return self.resolve_security_report_times(record, rows)
        if category == "应急预案-应急资源备件":
            return self.resolve_schema_only(record, rows, row_index=1)
        return self.resolve_simple_header_table(record, rows, header_row=1, data_start=2, strategy="rule_simple_header")

    def table_result(
        self,
        record: dict[str, Any],
        rows: list[dict[str, Any]],
        segments: list[dict[str, Any]],
        *,
        strategy: str,
        skipped_rows: dict[int, str],
    ) -> dict[str, Any]:
        row_indices = {int(row["row_index"]) for row in rows}
        covered = {idx for segment in segments for idx in segment.get("source_row_indices", [])}
        intentionally_skipped = set(skipped_rows)
        unknown_skipped = sorted(row_indices - covered - intentionally_skipped)
        fallback_count = sum(len(seg.get("quality_flags", {}).get("fallback_headers") or []) for seg in segments)
        placeholder_count = sum(1 for seg in segments if seg.get("quality_flags", {}).get("has_placeholder_column_label"))
        status = "resolved"
        if not segments:
            status = "no_segments"
        elif all(seg.get("segment_role") == "schema" for seg in segments):
            status = "schema_only"
        elif unknown_skipped or placeholder_count:
            status = "needs_review"
        confidence = "high"
        if status in {"schema_only", "needs_review"} or fallback_count:
            confidence = "medium"
        if status == "no_segments" or placeholder_count or unknown_skipped:
            confidence = "low"
        result = base_table_record(record, rows, strategy)
        result.update(
            {
                "status": status,
                "confidence": confidence,
                "segment_count": len(segments),
                "covered_rows": sorted(covered),
                "skipped_rows": {str(key): value for key, value in sorted(skipped_rows.items())},
                "unknown_skipped_rows": unknown_skipped,
                "fallback_header_count": fallback_count,
                "placeholder_segment_count": placeholder_count,
            }
        )
        return result

    def resolve_simple_header_table(
        self,
        record: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        header_row: int,
        data_start: int,
        strategy: str,
        headers: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows_by_index = row_map(rows)
        headers = headers or row_values(rows_by_index, header_row)
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped = {header_row: "header"}
        for row in rows:
            if row["row_index"] < data_start:
                skipped.setdefault(row["row_index"], "header_or_context")
                continue
            values = row["row_values"]
            if not any(not is_blank(value) for value in values):
                skipped[row["row_index"]] = "blank"
                continue
            raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy=strategy, segment_role="data_row", headers=headers, values=values, raw_text=raw, context=context, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy=strategy, skipped_rows=skipped)

    def resolve_schema_only(self, record: dict[str, Any], rows: list[dict[str, Any]], *, row_index: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows_by_index = row_map(rows)
        row = rows_by_index.get(row_index) or (rows[0] if rows else {})
        headers = row.get("row_values") or []
        context = context_from_rows(rows, record.get("table_category", ""))
        raw = "字段：" + "、".join(clean_header(value) for value in headers if clean_header(value))
        segment = make_segment(record, rows, [row], strategy="rule_schema_only", segment_role="schema", headers=headers, values=headers, raw_text=raw, context=context)
        return [segment], self.table_result(record, rows, [segment], strategy="rule_schema_only", skipped_rows={})

    def resolve_department(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows_by_index = row_map(rows)
        context = context_from_rows(rows, record.get("table_category", ""))
        subject = table_subject_from_row(rows_by_index.get(1))
        segments: list[dict[str, Any]] = []
        skipped: dict[int, str] = {}
        row1 = rows_by_index.get(1)
        if row1:
            values = compact_semantic_values(row1["row_values"])
            raw, fallback = alternating_pairs(values)
            segments.append(make_segment(record, rows, [row1], strategy="rule_department_profile", segment_role="metadata", headers=[], values=values, raw_text=raw, context=context, fallback_headers=fallback, table_subject=subject))
        responsibility_headers = ["序号", "工作领域", "职责/任务"]
        current_group = ""
        for row in rows:
            idx = row["row_index"]
            if idx == 1:
                continue
            values = row["row_values"]
            if idx == 6 or values[:3] == ["序号", "工作领域", "内部分配主要职责、任务"]:
                skipped[idx] = "inner_responsibility_header"
                continue
            values = compact_semantic_values(values)
            if len(values) == 1 and values[0]:
                current_group = values[0]
                raw = f"栏目：{values[0]}"
                segments.append(make_segment(record, rows, [row], strategy="rule_department_profile", segment_role="context", headers=["栏目"], values=values, raw_text=with_subject(subject, raw), context=context, group=current_group, table_subject=subject))
                continue
            if idx < 6:
                field = clean_header(values[0]) if values else ""
                content = values[1] if len(values) > 1 else ""
                raw = f"{field}：{content}" if content else f"栏目：{field}"
                segments.append(make_segment(record, rows, [row], strategy="rule_department_profile", segment_role="kv", headers=["字段", "内容"], values=values, raw_text=with_subject(subject, raw), context=context, table_subject=subject))
            else:
                raw, fallback = pair_values(responsibility_headers, values)
                segments.append(make_segment(record, rows, [row], strategy="rule_department_profile", segment_role="data_row", headers=responsibility_headers, values=values, raw_text=with_subject(subject, raw), context=context, group=current_group, fallback_headers=fallback, table_subject=subject))
        return segments, self.table_result(record, rows, segments, strategy="rule_department_profile", skipped_rows=skipped)

    def resolve_position(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = context_from_rows(rows, record.get("table_category", ""))
        subject = table_subject_from_row(rows[0] if rows else None)
        segments: list[dict[str, Any]] = []
        current_group = ""
        for row in rows:
            values = compact_semantic_values(row["row_values"])
            idx = row["row_index"]
            if idx == 1:
                raw, fallback = alternating_pairs(values)
                segments.append(make_segment(record, rows, [row], strategy="rule_position_profile", segment_role="metadata", headers=[], values=values, raw_text=raw, context=context, fallback_headers=fallback, table_subject=subject))
                continue
            if len(values) == 1:
                current_group = values[0]
                raw = f"栏目：{values[0]}"
                segments.append(make_segment(record, rows, [row], strategy="rule_position_profile", segment_role="context", headers=["栏目"], values=values, raw_text=with_subject(subject, raw), context=context, group=current_group, table_subject=subject))
                continue
            if len(values) > 2 and len(values) % 2 == 0:
                headers = []
                raw, fallback = alternating_pairs(values)
            else:
                headers = ["字段", "内容"]
                raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy="rule_position_profile", segment_role="kv", headers=headers, values=values, raw_text=with_subject(subject, raw), context=context, group=current_group, fallback_headers=fallback, table_subject=subject))
        return segments, self.table_result(record, rows, segments, strategy="rule_position_profile", skipped_rows={})

    def resolve_score_grade(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows_by_index = row_map(rows)
        headers = row_values(rows_by_index, 1)
        levels = row_values(rows_by_index, 2)
        meanings = row_values(rows_by_index, 3)
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        for col in range(1, len(headers)):
            values = [headers[col], levels[col] if col < len(levels) else "", meanings[col] if col < len(meanings) else ""]
            raw, fallback = pair_values(["分数段", "等级", "意义"], values)
            segments.append(make_segment(record, rows, [rows_by_index[2], rows_by_index[3]], strategy="rule_pivot_score_grade", segment_role="pivot_column", headers=["分数段", "等级", "意义"], values=values, raw_text=raw, context=context, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="rule_pivot_score_grade", skipped_rows={1: "header"})

    def resolve_performance_detail(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        hint = self.hints.get((record.get("file_name", ""), record.get("anchor", "")))
        headers = ["测评项目", "评分标准", "评分", "备注"]
        data_rows = {3, 4, 5, 6, 7, 8, 9}
        title_rows = {1}
        header_rows = {2}
        llm_hint_id = None
        if hint:
            llm_hint_id = hint.get("hint_id")
            llm = hint.get("llm_hint") or {}
            headers = [display_text(item) for item in llm.get("column_headers") or headers]
            data_rows = set(llm.get("data_rows") or sorted(data_rows))
            title_rows = set(llm.get("title_rows") or [1])
            header_rows = set(llm.get("header_rows") or [2])
        rows_by_index = row_map(rows)
        context = "省级数据中心协维年度绩效考核内容"
        segments: list[dict[str, Any]] = []
        skipped = {idx: "title" for idx in title_rows}
        skipped.update({idx: "header" for idx in header_rows})
        for idx in sorted(data_rows):
            row = rows_by_index.get(idx)
            if not row:
                continue
            values = compact_semantic_values(row["row_values"])
            raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy="llm_hint_plus_rule_performance" if llm_hint_id else "rule_performance_detail", segment_role="data_row", headers=headers, values=values, raw_text=raw, context=context, llm_hint_id=llm_hint_id, fallback_headers=fallback))
        remark = rows_by_index.get(10)
        if remark:
            values = compact_semantic_values(remark["row_values"])
            if values and clean_header(values[0]) == "备注" and len(values) > 1:
                raw = f"备注：{values[1]}"
                fallback = []
            else:
                raw, fallback = pair_values(["备注", "说明"], values)
            segments.append(make_segment(record, rows, [remark], strategy="rule_performance_detail", segment_role="note", headers=["备注", "说明"], values=values, raw_text=raw, context=context, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="llm_hint_plus_rule_performance" if llm_hint_id else "rule_performance_detail", skipped_rows=skipped)

    def resolve_ip_segments(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        hint = self.hints.get((record.get("file_name", ""), record.get("anchor", "")))
        headers = ["地址类型", "互联地址", "业务地址"]
        data_rows = {3, 4, 5, 6}
        llm_hint_id = None
        if hint:
            llm_hint_id = hint.get("hint_id")
            llm = hint.get("llm_hint") or {}
            headers = [display_text(item) for item in llm.get("column_headers") or headers]
            data_rows = set(llm.get("data_rows") or sorted(data_rows))
        rows_by_index = row_map(rows)
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        for idx in sorted(data_rows):
            row = rows_by_index.get(idx)
            if not row:
                continue
            raw, fallback = pair_values(headers, row["row_values"])
            segments.append(make_segment(record, rows, [row], strategy="llm_hint_plus_rule_ip" if llm_hint_id else "rule_ip_segments", segment_role="data_row", headers=headers, values=row["row_values"], raw_text=raw, context=context, llm_hint_id=llm_hint_id, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="llm_hint_plus_rule_ip" if llm_hint_id else "rule_ip_segments", skipped_rows={1: "title", 2: "header"})

    def resolve_cabinet_distribution(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        data_rows = [row for row in rows if row["row_index"] > 2]
        inferred_first_column: dict[int, str] = {}
        current_center = ""
        for index, row in enumerate(data_rows):
            values = row["row_values"]
            if len(values) >= 6 and not is_blank(values[0]) and display_text(values[0]) != "合计":
                current_center = display_text(values[0])
            if len(values) >= 6 and is_blank(values[0]):
                next_center = ""
                for later in data_rows[index + 1 :]:
                    later_values = later["row_values"]
                    if len(later_values) >= 6 and not is_blank(later_values[0]) and display_text(later_values[0]) != "合计":
                        next_center = display_text(later_values[0])
                        break
                inferred_first_column[row["row_index"]] = current_center or next_center
        for row in rows:
            idx = row["row_index"]
            if idx in {1, 2}:
                continue
            values = list(row["row_values"])
            if idx in inferred_first_column and len(values) >= 6:
                values[0] = inferred_first_column[idx]
            headers = ["数据中心名称", "机房", "机柜总数", "已使用", "未使用", "具体机柜分布情况"]
            if len(values) == 4:
                headers = ["机房", "机柜总数", "已使用", "具体机柜分布情况"]
            elif len(values) == 5:
                headers = ["数据中心名称", "机房", "机柜总数", "已使用", "具体机柜分布情况"]
            raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy="rule_cabinet_distribution", segment_role="data_row", headers=headers, values=values, raw_text=raw, context=context, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="rule_cabinet_distribution", skipped_rows={1: "title", 2: "header"})

    def resolve_temperature_humidity(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        headers = ["监测区域", "本月最高温度(℃)", "本月最低温度(℃)", "月平均温度(℃)", "本月最高湿度(%RH)", "本月最低湿度(%RH)", "月平均湿度(%RH)"]
        return self.resolve_simple_header_table(record, rows, header_row=2, data_start=3, headers=headers, strategy="rule_temperature_humidity")

    def resolve_title_header_table(self, record: dict[str, Any], rows: list[dict[str, Any]], headers: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self.resolve_simple_header_table(record, rows, header_row=2, data_start=3, headers=headers, strategy="rule_title_header_table")

    def resolve_questionnaire(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        hint = self.hints.get((record.get("file_name", ""), record.get("anchor", "")))
        headers = ["评价项", "非常满意", "满意", "一般", "不满意", "非常不满意"]
        llm_hint_id = hint.get("hint_id") if hint else None
        if hint:
            llm = hint.get("llm_hint") or {}
            headers = [display_text(item) for item in llm.get("column_headers") or headers]
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped: dict[int, str] = {}
        current_group = ""
        for row in rows:
            values = compact_semantic_values(row["row_values"])
            idx = row["row_index"]
            checkbox_count = sum(1 for value in values[1:] if display_text(value).startswith("□"))
            if len(values) == 1 or (values and values[0].endswith("方面") and checkbox_count >= 3):
                current_group = values[0] if values else current_group
                skipped[idx] = "questionnaire_group"
                continue
            raw, fallback = pair_values(headers, values)
            raw = f"满意度问卷模板项；{raw}"
            segments.append(make_segment(record, rows, [row], strategy="llm_hint_plus_rule_questionnaire" if llm_hint_id else "rule_questionnaire", segment_role="questionnaire_option", headers=headers, values=values, raw_text=raw, context=context, group=current_group, llm_hint_id=llm_hint_id, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="llm_hint_plus_rule_questionnaire" if llm_hint_id else "rule_questionnaire", skipped_rows=skipped)

    def append_table_aggregate(
        self,
        record: dict[str, Any],
        rows: list[dict[str, Any]],
        segments: list[dict[str, Any]],
        *,
        strategy: str,
        segment_role: str,
        context: str,
        group: str = "",
        title: str = "完整表格内容",
    ) -> None:
        data_segments = [segment for segment in segments if segment.get("segment_role") != segment_role]
        if len(data_segments) <= 1:
            return
        rows_by_index = row_map(rows)
        source_rows = [
            rows_by_index[index]
            for segment in data_segments
            for index in segment.get("source_row_indices", [])
            if index in rows_by_index
        ]
        seen: set[int] = set()
        unique_source_rows: list[dict[str, Any]] = []
        for row in source_rows:
            idx = int(row.get("row_index") or 0)
            if idx in seen:
                continue
            seen.add(idx)
            unique_source_rows.append(row)
        parts = [f"{idx + 1}. {segment.get('raw_text')}" for idx, segment in enumerate(data_segments)]
        raw = f"{title}：" + "；".join(parts)
        segments.append(
            make_segment(
                record,
                rows,
                unique_source_rows,
                strategy=strategy,
                segment_role=segment_role,
                headers=[],
                values=[],
                raw_text=raw,
                context=context,
                group=group,
            )
        )

    def resolve_step_instruction(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        for row in rows:
            values = compact_semantic_values(row["row_values"])
            if not values:
                continue
            raw = f"步骤{len(segments) + 1}：{values[0]}"
            segments.append(make_segment(record, rows, [row], strategy="rule_single_column_steps", segment_role="instruction_step", headers=["步骤说明"], values=values, raw_text=raw, context=context))
        self.append_table_aggregate(record, rows, segments, strategy="rule_single_column_steps", segment_role="procedure", context=context, title="零信任登录4A完整步骤")
        return segments, self.table_result(record, rows, segments, strategy="rule_single_column_steps", skipped_rows={})

    def resolve_single_statement(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        for row in rows:
            values = compact_semantic_values(row["row_values"])
            if not values:
                continue
            raw = f"说明：{values[0]}"
            segments.append(make_segment(record, rows, [row], strategy="rule_single_statement", segment_role="statement", headers=["说明"], values=values, raw_text=raw, context=context))
        return segments, self.table_result(record, rows, segments, strategy="rule_single_statement", skipped_rows={})

    def resolve_org_team(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped: dict[int, str] = {1: "top_group", 2: "header"}
        current_group = compact_semantic_values(row_values(row_map(rows), 1))[0] if rows else ""
        for row in rows:
            idx = row["row_index"]
            values = compact_semantic_values(row["row_values"])
            if idx <= 2:
                continue
            if len(values) == 1:
                current_group = values[0]
                skipped[idx] = "group"
                continue
            headers = ["单位/厂家/专业", "职责/岗位"] if len(values) == 2 else ["单位", "姓名", "职务", "职责", "电话"]
            raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy="rule_emergency_org_team", segment_role="data_row", headers=headers, values=values, raw_text=raw, context=context, group=current_group, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="rule_emergency_org_team", skipped_rows=skipped)

    def resolve_network_devices(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        headers = ["序号", "机房", "设备名称", "端口总数", "GE端口总数", "10GE端口总数", "10GE占用", "10GE不可用", "10GE空闲", "40GE总数", "40GE占用", "40GE不可用", "40GE空闲"]
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped = {1: "header_level_1", 2: "header_level_2"}
        for row in rows:
            if row["row_index"] <= 2:
                continue
            values = row["row_values"]
            raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy="rule_emergency_network_devices", segment_role="data_row", headers=headers, values=values, raw_text=raw, context=context, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="rule_emergency_network_devices", skipped_rows=skipped)

    def resolve_response_roles(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped = {1: "header"}
        for row in rows:
            if row["row_index"] == 1:
                continue
            values = row["row_values"]
            if len(values) >= 3 and not display_text(values[0]):
                semantic_values = [values[1], values[2]]
            else:
                semantic_values = compact_semantic_values(values)
            raw, fallback = pair_values(["角色", "职责"], semantic_values)
            segments.append(make_segment(record, rows, [row], strategy="rule_response_roles", segment_role="data_row", headers=["角色", "职责"], values=semantic_values, raw_text=raw, context=context, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="rule_response_roles", skipped_rows=skipped)

    def resolve_command_example(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        table_no = int(record.get("table_no") or 0)
        if table_no in {17, 18}:
            return self.resolve_simple_header_table(record, rows, header_row=0, data_start=1, headers=["厂商", "命令"], strategy="rule_emergency_vendor_command")
        headers = row_values(row_map(rows), 1)
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped = {1: "header"}
        for row in rows:
            if row["row_index"] == 1:
                continue
            values = compact_semantic_values(row["row_values"])
            if not any(not is_blank(value) for value in values):
                skipped[row["row_index"]] = "blank"
                continue
            raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy="rule_emergency_command_matrix", segment_role="command_row", headers=headers, values=values, raw_text=raw, context=context, fallback_headers=fallback))
        self.append_table_aggregate(record, rows, segments, strategy="rule_emergency_command_matrix", segment_role="table_procedure", context=context, title="完整命令序列")
        return segments, self.table_result(record, rows, segments, strategy="rule_emergency_command_matrix", skipped_rows=skipped)

    def resolve_fault_command(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if int(record.get("table_no") or 0) == 24:
            return self.resolve_optical_module_specs(record, rows)
        headers = row_values(row_map(rows), 1)
        strategy = "rule_emergency_fault_command"
        if headers == ["属性", "描述"]:
            strategy = "rule_emergency_attribute_desc"
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped = {1: "header"}
        vendor_names = {"华为", "诺基亚", "中兴", "锐捷", "华三"}
        for row in rows:
            if row["row_index"] == 1:
                continue
            values = row["row_values"]
            compacted_values = compact_semantic_values(values)
            if compacted_values and set(compacted_values).issubset(vendor_names) and len(compacted_values) >= 2:
                skipped[row["row_index"]] = "vendor_group_header"
                continue
            raw, fallback = pair_values(headers, values)
            segments.append(make_segment(record, rows, [row], strategy=strategy, segment_role="data_row", headers=headers, values=values, raw_text=raw, context=context, fallback_headers=fallback))
        if int(record.get("table_no") or 0) != 24:
            self.append_table_aggregate(record, rows, segments, strategy=strategy, segment_role="table_procedure", context=context, title="完整排查步骤")
        return segments, self.table_result(record, rows, segments, strategy=strategy, skipped_rows=skipped)

    def resolve_optical_module_specs(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        context = context_from_rows(rows, record.get("table_category", ""))
        segments: list[dict[str, Any]] = []
        skipped: dict[int, str] = {}
        group = "CFP/CFP2/QSFP28"
        headers = ["属性", "CFP", "CFP2", "QSFP28"]
        repeated_variant_headers: list[str] = []
        pending_distance_headers: list[str] = []
        for row in rows:
            idx = row["row_index"]
            values = row["row_values"]
            compacted = compact_semantic_values(values)
            if compacted in (["属性", "描述"], ["属性", "描述："]):
                skipped[idx] = "header"
                continue
            if len(compacted) == 1:
                group = compacted[0]
                pending_distance_headers = []
                skipped[idx] = "group"
                continue
            if idx == 2:
                repeated_variant_headers = [display_text(value) for value in values[1:] if display_text(value)]
                group = "/".join(compact_semantic_values(values))
                headers = ["属性"] + compact_semantic_values(values)
                skipped[idx] = "variant_header"
                continue
            if compacted and compacted[0] == "传输距离" and len(compacted) > 2:
                pending_distance_headers = compacted[1:]
                headers = ["属性"] + pending_distance_headers
                raw = "传输距离选项：" + "、".join(pending_distance_headers)
                segments.append(make_segment(record, rows, [row], strategy="rule_emergency_optical_module_specs", segment_role="distance_options", headers=["适用距离"], values=compacted, raw_text=raw, context=context, group=group))
                continue
            if repeated_variant_headers and len(values) - 1 == len(repeated_variant_headers):
                by_variant: dict[str, list[str]] = defaultdict(list)
                for variant, value in zip(repeated_variant_headers, values[1:]):
                    if display_text(value) and display_text(value) not in by_variant[variant]:
                        by_variant[variant].append(display_text(value))
                semantic_values = [values[0]] + ["、".join(by_variant[variant]) for variant in compact_semantic_values(repeated_variant_headers)]
                semantic_headers = ["属性"] + compact_semantic_values(repeated_variant_headers)
            else:
                semantic_values = compacted
                semantic_headers = headers
            raw, fallback = pair_values(semantic_headers, semantic_values)
            segments.append(make_segment(record, rows, [row], strategy="rule_emergency_optical_module_specs", segment_role="data_row", headers=semantic_headers, values=semantic_values, raw_text=raw, context=context, group=group, fallback_headers=fallback))
        return segments, self.table_result(record, rows, segments, strategy="rule_emergency_optical_module_specs", skipped_rows=skipped)

    def resolve_security_report_times(self, record: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        headers = [
            "事件级别",
            "处置管理小组汇报网络与信息安全小组时限",
            "处置管理小组汇报网络与信息安全小组周期",
            "网络与信息安全小组汇报领导小组时限",
            "网络与信息安全小组汇报领导小组周期",
        ]
        return self.resolve_simple_header_table(record, rows, header_row=2, data_start=3, headers=headers, strategy="rule_security_report_times")


def audit_resolution(
    candidates: list[dict[str, Any]],
    table_results: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    status_counts = Counter(item["status"] for item in table_results)
    confidence_counts = Counter(item["confidence"] for item in table_results)
    strategy_counts = Counter(item["strategy"] for item in table_results)
    category_table_counts = Counter(item["table_category"] for item in table_results)
    category_segment_counts = Counter(item["table_category"] for item in segments)
    issue_segments = [
        item
        for item in segments
        if item.get("quality_flags", {}).get("has_placeholder_column_label")
        or item.get("quality_flags", {}).get("empty_raw_text")
        or item.get("quality_flags", {}).get("fallback_headers")
    ]
    table_issues = [
        item
        for item in table_results
        if item.get("status") in {"needs_review", "no_segments"}
        or item.get("unknown_skipped_rows")
        or item.get("placeholder_segment_count")
    ]
    processed_rate = len(table_results) / len(candidates) if candidates else 0
    high_or_medium = sum(1 for item in table_results if item["confidence"] in {"high", "medium"})
    estimated_accuracy = high_or_medium / len(table_results) if table_results else 0
    preservation_failures: list[dict[str, Any]] = []
    for segment in segments:
        raw_compare = compare_text(segment.get("raw_text"))
        for value in segment.get("row_values") or []:
            value_compare = compare_text(value)
            if value_compare in {compare_text(item) for item in BLANK_MARKERS}:
                continue
            if value_compare and value_compare not in raw_compare:
                preservation_failures.append(
                    {
                        "segment_id": segment.get("segment_id"),
                        "value": value,
                        "raw_text": segment.get("raw_text"),
                    }
                )
                break
    return {
        "candidate_tables": len(candidates),
        "processed_tables": len(table_results),
        "processed_rate": round(processed_rate, 4),
        "resolved_segments": len(segments),
        "status_counts": dict(status_counts),
        "confidence_counts": dict(confidence_counts),
        "strategy_counts": dict(strategy_counts),
        "category_table_counts": dict(category_table_counts),
        "category_segment_counts": dict(category_segment_counts),
        "placeholder_segment_count": sum(1 for item in segments if item.get("quality_flags", {}).get("has_placeholder_column_label")),
        "fallback_header_segment_count": sum(1 for item in segments if item.get("quality_flags", {}).get("fallback_headers")),
        "schema_only_tables": status_counts.get("schema_only", 0),
        "needs_review_tables": len(table_issues),
        "estimated_rule_accuracy": round(estimated_accuracy, 4),
        "source_value_preservation_failures": len(preservation_failures),
        "source_value_preservation_failure_samples": preservation_failures[:10],
        "issue_table_ids": [item["table_id"] for item in table_issues[:50]],
        "issue_segment_ids": [item["segment_id"] for item in issue_segments[:50]],
    }


def write_visualization(out_dir: Path, summary: dict[str, Any], table_results: list[dict[str, Any]], segments: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# Step 09 低置信嵌入 Word 表全量处理\n")
    lines.append("本步骤处理 Step 07 标出的嵌入 Word 低置信表。正文全部来自 04B 的原始 `row_values`；结构来自确定性模板规则，少数表可消费 Step 08 的 LLM 结构 hint。\n")
    lines.append("## 总览\n")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    for key in [
        "candidate_tables",
        "processed_tables",
        "processed_rate",
        "resolved_segments",
        "schema_only_tables",
        "needs_review_tables",
        "placeholder_segment_count",
        "fallback_header_segment_count",
        "source_value_preservation_failures",
        "estimated_rule_accuracy",
    ]:
        lines.append(f"| `{key}` | {summary.get(key)} |")
    lines.append("\n## 表状态\n")
    lines.append("| status | count |")
    lines.append("|---|---:|")
    for key, value in sorted(summary.get("status_counts", {}).items()):
        lines.append(f"| `{key}` | {value} |")
    lines.append("\n## 策略分布\n")
    lines.append("| strategy | count |")
    lines.append("|---|---:|")
    for key, value in sorted(summary.get("strategy_counts", {}).items()):
        lines.append(f"| `{key}` | {value} |")
    lines.append("\n## 按表类型统计\n")
    lines.append("| table_category | tables | segments |")
    lines.append("|---|---:|---:|")
    table_counts = summary.get("category_table_counts", {})
    segment_counts = summary.get("category_segment_counts", {})
    for category, count in sorted(table_counts.items()):
        lines.append(f"| {md(category, 60)} | {count} | {segment_counts.get(category, 0)} |")
    lines.append("\n## 样例\n")
    lines.append("| type | file | anchor | rows | text |")
    lines.append("|---|---|---|---|---|")
    seen_categories: set[str] = set()
    for segment in segments:
        category = segment.get("table_category", "")
        if category in seen_categories:
            continue
        seen_categories.add(category)
        rows = ",".join(str(item) for item in segment.get("source_row_indices", []))
        lines.append(
            f"| {md(category, 45)} | `{md(segment.get('file_name'), 45)}` | `{md(segment.get('anchor'), 36)}` | "
            f"{rows} | {md(segment.get('embedding_text'), 220)} |"
        )
    lines.append("\n## 需关注表\n")
    issue_tables = [
        item
        for item in table_results
        if item.get("status") in {"needs_review", "no_segments"}
        or item.get("unknown_skipped_rows")
        or item.get("placeholder_segment_count")
    ]
    if not issue_tables:
        lines.append("当前没有 `needs_review/no_segments` 表；`schema_only` 表表示原文只有表头没有数据行。")
    else:
        lines.append("| status | category | file | anchor | issue |")
        lines.append("|---|---|---|---|---|")
        for item in issue_tables[:80]:
            issue = f"unknown_skipped={item.get('unknown_skipped_rows')}; placeholders={item.get('placeholder_segment_count')}"
            lines.append(f"| `{item['status']}` | {md(item['table_category'])} | `{md(item['file_name'], 50)}` | `{md(item['anchor'], 40)}` | {md(issue)} |")
    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def md(value: Any, limit: int = 120) -> str:
    text = display_text(value)
    if len(text) > limit:
        text = text[: limit - 1] + "..."
    return text.replace("|", "\\|")


def run(
    classification_path: Path = DEFAULT_CLASSIFICATION,
    embedded_segments_path: Path = DEFAULT_EMBEDDED_SEGMENTS,
    hints_path: Path = DEFAULT_HINTS,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = read_json(classification_path)
    grouped_rows = group_embedded_table_rows(embedded_segments_path)
    hints = load_hints(hints_path)
    resolver = Resolver(hints)

    all_segments: list[dict[str, Any]] = []
    table_results: list[dict[str, Any]] = []
    for candidate in candidates:
        rows = grouped_rows.get(candidate_key(candidate), [])
        if not rows:
            result = base_table_record(candidate, [], "missing_source_rows")
            result.update(
                {
                    "status": "no_segments",
                    "confidence": "low",
                    "segment_count": 0,
                    "covered_rows": [],
                    "skipped_rows": {},
                    "unknown_skipped_rows": [],
                    "fallback_header_count": 0,
                    "placeholder_segment_count": 0,
                    "missing_reason": "candidate table rows not found in embedded_segments.jsonl",
                }
            )
            table_results.append(result)
            continue
        segments, result = resolver.resolve(candidate, rows)
        all_segments.extend(segments)
        table_results.append(result)

    summary = audit_resolution(candidates, table_results, all_segments)
    write_jsonl(out_dir / "resolved_table_segments.jsonl", all_segments)
    write_jsonl(out_dir / "resolved_tables.jsonl", table_results)
    write_json(out_dir / "summary.json", summary)
    write_visualization(out_dir, summary, table_results, all_segments)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve low-confidence embedded Word table candidates from Step 07.")
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--embedded-segments", type=Path, default=DEFAULT_EMBEDDED_SEGMENTS)
    parser.add_argument("--hints", type=Path, default=DEFAULT_HINTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    summary = run(args.classification, args.embedded_segments, args.hints, args.out_dir)
    print(f"resolved {summary['processed_tables']} tables into {summary['resolved_segments']} segments -> {args.out_dir}")


if __name__ == "__main__":
    main()
