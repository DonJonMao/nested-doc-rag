from __future__ import annotations

from nested_doc_rag.evaluation.field_metrics import FieldEvaluation, badcase_counts
from nested_doc_rag.io import md

METRIC_LABELS = {
    "field_exact_match": "字段精确匹配率",
    "field_semantic_match": "字段语义匹配率",
    "answer_status_accuracy": "回答状态准确率",
    "evidence_support_rate": "证据支撑率",
    "evidence_recall_at_k": "证据召回率",
    "abstention_precision": "拒答精确率",
    "constraint_violation_rate": "约束违规率",
    "human_review_rate": "人工审核率",
    "correction_required_rate": "需修正率",
}


def render_field_eval_markdown(result: FieldEvaluation, *, evidence_k: int = 5) -> str:
    lines: list[str] = []
    lines.append("# 字段级评测报告\n")
    lines.append("## 整体指标\n")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    for key, label in METRIC_LABELS.items():
        value = result.metrics.get(key, 0)
        metric_name = f"{label} ({key})"
        if key == "evidence_recall_at_k":
            metric_name = f"{label}@{evidence_k} ({key})"
        lines.append(f"| {metric_name} | {float(value):.4f} |")
    lines.append(f"| 字段数 (field_count) | {int(result.metrics.get('field_count', 0))} |")

    counts = badcase_counts(result.rows)
    lines.append("\n## Badcase 分类\n")
    lines.append("| category | count |")
    lines.append("|---|---:|")
    if counts:
        for category, count in counts.items():
            lines.append(f"| `{category}` | {count} |")
    else:
        lines.append("| `none` | 0 |")

    lines.append("\n## Badcase 明细\n")
    lines.append("| field_id | row | cell | type | categories | expected | predicted | status | confidence |")
    lines.append("|---|---:|---|---|---|---|---|---|---:|")
    for row in result.rows:
        if not row.badcase_categories:
            continue
        lines.append(
            f"| `{row.field_id}` | {row.row_index} | `{row.target_cell or ''}` | `{row.field_type}` | "
            f"{md(', '.join(row.badcase_categories), 100)} | {md(row.expected_value, 80)} | {md(row.answer_value, 80)} | "
            f"`{row.expected_status}->{row.answer_status}` | {row.confidence:.3f} |"
        )
    if not result.badcases:
        lines.append("| - | - | - | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"
