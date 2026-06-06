from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/07_agent_need_audit"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def table_no(anchor: str) -> int | None:
    match = re.search(r"table\s+(\d+)", anchor)
    return int(match.group(1)) if match else None


def classify(case: dict[str, Any]) -> tuple[str, str, str]:
    file_name = case.get("file_name", "")
    anchor = case.get("anchor", "")
    number = table_no(anchor)

    if file_name == "陕西移动IDC对外服务知识库.xlsx":
        if "!D3 " in anchor:
            if number in {1, 6, 11}:
                return ("original_26", "岗位职责-部门职责总览", "用父行 D3 作为章节上下文；按键值/职责段落规则重切。")
            return ("original_26", "岗位职责-岗位说明书", "用父行 D3 作为章节上下文；按岗位名称、所属部门、职责/任职要求字段重切。")
        if "!D8 " in anchor:
            if number == 1:
                return ("original_26", "绩效考核-指标权重", "表头较短但结构稳定；按考核内容/权重规则重切。")
            if number == 2:
                return ("original_26", "绩效考核-年度考核明细", "多级标题和占位列名较多；建议 LLM 只判断表头层级。")
            return ("original_26", "绩效考核-评分档位", "按分数段映射规则重切。")
        if "!D9 " in anchor:
            mapping = {
                1: ("服务报告-资源机柜分布", "按数据中心/机房/机柜使用情况字段重切。"),
                3: ("服务报告-IP地址段", "标题行像表头；建议 LLM 判断标题行与数据行。"),
                6: ("服务报告-基础维护键值表", "按基本情况/数值/说明三列重切。"),
                7: ("服务报告-温湿度监控", "按监测区域/温度/湿度重切；占位列名可规则修正。"),
                8: ("服务报告-重大事件总结", "标题型表格；按标题+条目规则重切。"),
                9: ("服务报告-未达标总结", "标题型表格；按标题+条目规则重切。"),
                11: ("服务报告-满意度评价", "问卷矩阵类表；建议 LLM 判断题组、选项列、评价项。"),
            }
            if number in mapping:
                category, action = mapping[number]
                return ("original_26", category, action)

    if number == 1:
        return ("new_from_rar_emergency", "应急预案-版本记录", "模板稳定；按版本/日期/修改/核定/说明/状态重切。")
    if number == 3:
        return ("new_from_rar_emergency", "应急预案-网络设备清单", "模板稳定；扩展表头词库后规则可处理。")
    if number == 4:
        return ("new_from_rar_emergency", "应急预案-零信任登录4A步骤", "单列步骤说明表；按步骤序号+说明重切。")
    if number == 5:
        return ("new_from_rar_emergency", "应急预案-保障队伍组织", "单列表/组织架构类；按章节+列表项重切。")
    if number == 8:
        return ("new_from_rar_emergency", "应急预案-应急处理原则", "单句原则说明；作为说明型知识块保留。")
    if number == 9:
        return ("new_from_rar_emergency", "应急预案-响应角色职责", "按人员/角色/职责三列重切。")
    if number and 10 <= number <= 18:
        return ("new_from_rar_emergency", "应急预案-链路故障操作示例", "命令示例矩阵；按场景+厂商+步骤重切。")
    if number and 19 <= number <= 24:
        return ("new_from_rar_emergency", "应急预案-设备故障排查命令", "命令矩阵；按故障类型+厂商+查询步骤重切。")
    if number == 25:
        return ("new_from_rar_emergency", "应急预案-安全事件上报时限", "按事件级别和上报链路重切。")
    if number == 26:
        return ("new_from_rar_emergency", "应急预案-应急资源备件", "按厂家/设备/板卡型号/数量重切。")
    return ("unknown", "其他低置信表", "人工抽样确认后再定规则。")


def run(out_dir: Path = OUT_DIR) -> list[dict[str, Any]]:
    cases = [
        case
        for case in read_jsonl(out_dir / "agent_need_cases.jsonl")
        if case.get("need_type") == "agent_candidate" and case.get("category") == "embedded_word_table_structure"
    ]
    records: list[dict[str, Any]] = []
    for case in cases:
        source_group, table_category, suggested_handling = classify(case)
        evidence = case.get("evidence", {})
        rows_sample = evidence.get("rows_sample") or []
        headers_sample = evidence.get("headers_sample") or []
        records.append(
            {
                "source_group": source_group,
                "table_category": table_category,
                "file_name": case.get("file_name"),
                "anchor": case.get("anchor"),
                "table_no": table_no(case.get("anchor", "")),
                "parent_attachment_id": evidence.get("parent_attachment_id"),
                "embedded_file_name": evidence.get("embedded_file_name"),
                "embedded_file_type": evidence.get("embedded_file_type"),
                "reason": case.get("reason"),
                "flags": {
                    "missing_section": evidence.get("missing_section"),
                    "placeholder": evidence.get("placeholder"),
                },
                "sample": rows_sample[0] if rows_sample else "",
                "header_sample": headers_sample[0] if headers_sample else [],
                "suggested_handling": suggested_handling,
            }
        )

    write_json(out_dir / "table_candidate_classification.json", records)
    write_markdown(out_dir / "table_candidate_classification.md", records)
    return records


def write_markdown(path: Path, records: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    lines.append("# 嵌入 Word 低置信表分类\n")
    lines.append("本报告只分类 `embedded_word_table_structure` 候选。RAR/旧 Word/PDF 下钻后，候选表从原来的 26 张扩展为当前 131 张。\n")

    lines.append("## 总览\n")
    lines.append("| group | count |")
    lines.append("|---|---:|")
    for key, value in Counter(item["source_group"] for item in records).items():
        lines.append(f"| `{key}` | {value} |")

    lines.append("\n## 按类型统计\n")
    lines.append("| group | table_category | count | suggested_handling |")
    lines.append("|---|---|---:|---|")
    sample_action: dict[tuple[str, str], str] = {}
    counter: Counter[tuple[str, str]] = Counter()
    for item in records:
        key = (item["source_group"], item["table_category"])
        counter[key] += 1
        sample_action[key] = item["suggested_handling"]
    for (group, category), count in sorted(counter.items()):
        lines.append(f"| `{group}` | {category} | {count} | {sample_action[(group, category)]} |")

    lines.append("\n## 原 26 张明细\n")
    write_group_table(lines, [item for item in records if item["source_group"] == "original_26"])

    lines.append("\n## 新增应急预案类明细\n")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        if item["source_group"] == "new_from_rar_emergency":
            grouped[item["table_category"]].append(item)
    for category, items in sorted(grouped.items()):
        lines.append(f"\n### {category} ({len(items)})\n")
        write_group_table(lines, items[:20])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def md(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "..."
    return text.replace("|", "\\|")


def write_group_table(lines: list[str], items: list[dict[str, Any]]) -> None:
    lines.append("| file | anchor | table | type | sample |")
    lines.append("|---|---|---:|---|---|")
    for item in items:
        lines.append(
            f"| `{md(item['file_name'], 60)}` | `{md(item['anchor'], 50)}` | "
            f"{item['table_no'] or ''} | {md(item['table_category'], 50)} | {md(item['sample'], 120)} |"
        )


if __name__ == "__main__":
    records = run()
    print(f"classified {len(records)} low-confidence embedded Word tables -> {OUT_DIR}")
