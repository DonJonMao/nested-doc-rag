from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SEGMENTS = Path(__file__).resolve().parents[1] / "artifacts/09_table_candidate_resolution/resolved_table_segments.jsonl"
OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/10_semantic_segment_audit"

BLANK_MARKERS = {"", "\\", "/", "／", "-", "—", "－"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def md(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "..."
    return text.replace("|", "\\|")


def nonblank_values(segment: dict[str, Any]) -> list[str]:
    values = []
    for value in segment.get("row_values") or []:
        text = " ".join(str(value or "").split())
        if text and text not in BLANK_MARKERS:
            values.append(text)
    return values


def table_key(segment: dict[str, Any]) -> tuple[str, str, str]:
    return (
        segment.get("file_name") or "",
        segment.get("anchor") or "",
        segment.get("embedded_file_name") or "",
    )


def audit_one(segment: dict[str, Any], aggregate_keys: set[tuple[str, str, str]]) -> dict[str, Any]:
    category = segment.get("table_category") or ""
    role = segment.get("segment_role") or ""
    raw = segment.get("raw_text") or ""
    flags: list[str] = []

    status = "ok"
    issue = ""
    recommendation = "可进入向量化。"
    embed_policy = "embed"
    if "右图" in raw or "如右图" in raw:
        flags.append("needs_image_evidence")

    if category == "应急预案-网络设备清单":
        values = nonblank_values(segment)
        if len(values) <= 2 and all(name not in raw for name in ["设备名称", "端口总数", "GE", "10GE", "40GE", "100GE"]):
            status = "low_value_incomplete_source"
            issue = "原始表行只解析出序号和机房，设备名称、端口数量等关键列为空；作为 RAG chunk 语义信息不足。"
            recommendation = "不要单独向量化；保留审计记录，后续回源检查该表是否由图片/合并单元格/不可解析对象承载设备信息。"
            embed_policy = "exclude"
    elif role == "schema":
        status = "schema_only"
        issue = "原表只有字段名，没有可回答问题的数据行。"
        recommendation = "可作为结构元数据保留，不建议进入普通事实向量库。"
        embed_policy = "metadata_only"
    elif category == "服务报告-满意度评价":
        status = "template_not_fact"
        issue = "这是满意度问卷模板选项，原文没有实际勾选结果；不能理解为客户给出的满意度事实。"
        recommendation = "向量化时标记为问卷模板；回答时避免表述为已评价结果。"
        embed_policy = "embed_as_template"
    elif role in {"procedure", "table_procedure"}:
        status = "ok_aggregate"
        issue = "这是为流程/命令类表新增的表级聚合块，适合回答完整步骤类问题。"
        recommendation = "优先进入向量库；召回后可附带对应逐行 chunk 作为细节来源。"
        embed_policy = "embed_preferred"
    elif table_key(segment) in aggregate_keys and role in {"instruction_step", "command_row", "data_row"} and category in {
        "应急预案-零信任登录4A步骤",
        "应急预案-链路故障操作示例",
        "应急预案-设备故障排查命令",
    }:
        status = "ok_row_with_aggregate"
        issue = "单行语义正确，但完整流程类问题应同时召回表级聚合块。"
        recommendation = "保留逐行 chunk；检索策略上让同表 procedure/table_procedure 有更高优先级。"
        embed_policy = "embed_with_parent"
    elif role == "context":
        status = "context_marker"
        issue = "这是栏目/分组提示，不是独立业务事实。"
        recommendation = "作为层级元数据保留；不建议单独进入事实向量库。"
        embed_policy = "metadata_only"
    elif flags:
        status = "ok_needs_image_evidence"
        issue = "文本引用了图片证据；文字语义可用，但回答时应关联后续图片佐证。"
        recommendation = "文本可向量化；图片按标签挂到同一父附件/行位置，回答时作为证据追加。"
        embed_policy = "embed_with_image_evidence"
    elif category.startswith("岗位职责"):
        if not segment.get("table_subject") and role != "metadata":
            status = "needs_subject_context"
            issue = "岗位/部门类 chunk 缺少岗位名称或部门名称，单独召回会丢主体。"
            recommendation = "需要把表格首行主体带入 chunk。"
            embed_policy = "exclude_until_fixed"
        else:
            status = "ok_subject_scoped"
            recommendation = "已带岗位/部门主体，可进入向量化。"
    elif category == "服务报告-资源机柜分布" and "三楼北机房" in raw and "数据中心名称" not in raw:
        status = "needs_merged_cell_inheritance"
        issue = "该行原始首列为空，但表格语义应继承合并单元格的数据中心名称。"
        recommendation = "需要用相邻非空单元格补齐合并单元格语义。"
        embed_policy = "exclude_until_fixed"

    if flags and status not in {"ok_needs_image_evidence"}:
        image_note = "文本引用图片证据；回答时应关联后续图片佐证。"
        issue = f"{issue} {image_note}".strip()
        if embed_policy == "embed":
            embed_policy = "embed_with_image_evidence"
        elif embed_policy in {"embed_with_parent", "embed_preferred"}:
            embed_policy = f"{embed_policy}_and_image"

    return {
        "semantic_status": status,
        "semantic_flags": flags,
        "semantic_issue": issue,
        "recommended_fix": recommendation,
        "embedding_policy": embed_policy,
    }


def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    segments = read_jsonl(SEGMENTS)
    aggregate_keys = {
        table_key(segment)
        for segment in segments
        if segment.get("segment_role") in {"procedure", "table_procedure"}
    }

    audited: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, 1):
        record = {
            "review_index": index,
            "segment_id": segment.get("segment_id"),
            "table_id": segment.get("table_id"),
            "table_category": segment.get("table_category"),
            "file_name": segment.get("file_name"),
            "anchor": segment.get("anchor"),
            "embedded_file_name": segment.get("embedded_file_name"),
            "source_row_indices": segment.get("source_row_indices"),
            "segment_role": segment.get("segment_role"),
            "context": segment.get("context"),
            "group": segment.get("group"),
            "table_subject": segment.get("table_subject"),
            "raw_text": segment.get("raw_text"),
        }
        record.update(audit_one(segment, aggregate_keys))
        audited.append(record)

    status_counts = Counter(record["semantic_status"] for record in audited)
    policy_counts = Counter(record["embedding_policy"] for record in audited)
    flag_counts = Counter(flag for record in audited for flag in record.get("semantic_flags", []))
    category_status_counts = Counter((record["table_category"], record["semantic_status"]) for record in audited)
    summary = {
        "total_segments": len(audited),
        "semantic_status_counts": dict(status_counts),
        "embedding_policy_counts": dict(policy_counts),
        "semantic_flag_counts": dict(flag_counts),
        "category_status_counts": {
            f"{category} / {status}": count
            for (category, status), count in sorted(category_status_counts.items())
        },
        "review_basis": "逐类人工语义审读后固化的规则标注；正文仍来自原始 row_values。",
    }

    with (OUT_DIR / "semantic_audit.jsonl").open("w", encoding="utf-8") as f:
        for record in audited:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_json(OUT_DIR / "semantic_audit_summary.json", summary)
    write_markdown(OUT_DIR / "semantic_audit.md", audited, summary)
    return summary


def write_markdown(path: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 10 Segment 语义审阅结论\n")
    lines.append("本文件是逐条 segment 的语义审阅结果。状态来自人工逐类审读后固化的标注规则，不替换原文内容。\n")
    lines.append("## 总览\n")
    lines.append("| status | count |")
    lines.append("|---|---:|")
    for status, count in sorted(summary["semantic_status_counts"].items()):
        lines.append(f"| `{status}` | {count} |")
    lines.append("\n## 向量化建议\n")
    lines.append("| policy | count |")
    lines.append("|---|---:|")
    for policy, count in sorted(summary["embedding_policy_counts"].items()):
        lines.append(f"| `{policy}` | {count} |")
    lines.append("\n## 语义附加标签\n")
    lines.append("| flag | count |")
    lines.append("|---|---:|")
    for flag, count in sorted(summary["semantic_flag_counts"].items()):
        lines.append(f"| `{flag}` | {count} |")

    lines.append("\n## 需要关注的条目\n")
    lines.append("| # | status | policy | category | anchor | embedded | rows | subject/group | issue | raw_text |")
    lines.append("|---:|---|---|---|---|---|---|---|---|---|")
    for record in records:
        if record["semantic_status"] in {"ok", "ok_subject_scoped"}:
            continue
        subject_group = record.get("table_subject") or record.get("group")
        lines.append(
            f"| {record['review_index']} | `{record['semantic_status']}` | `{record['embedding_policy']}` | "
            f"{md(record['table_category'], 34)} | `{md(record['anchor'], 34)}` | {md(record.get('embedded_file_name'), 34)} | "
            f"{md(record.get('source_row_indices'), 20)} | {md(subject_group, 42)} | {md(record.get('semantic_issue'), 120)} | {md(record.get('raw_text'), 180)} |"
        )

    lines.append("\n## 全量逐条清单\n")
    lines.append("| # | status | policy | category | role | anchor | rows | raw_text |")
    lines.append("|---:|---|---|---|---|---|---|---|")
    for record in records:
        lines.append(
            f"| {record['review_index']} | `{record['semantic_status']}` | `{record['embedding_policy']}` | "
            f"{md(record['table_category'], 34)} | `{md(record['segment_role'], 20)}` | `{md(record['anchor'], 34)}` | "
            f"{md(record.get('source_row_indices'), 20)} | {md(record.get('raw_text'), 180)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    summary = run()
    print(f"audited {summary['total_segments']} segments -> {OUT_DIR}")
