from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


SEGMENTS = Path(__file__).resolve().parents[1] / "artifacts/09_table_candidate_resolution/resolved_table_segments.jsonl"
OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/10_semantic_segment_audit"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def md(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "..."
    return text.replace("|", "\\|")


def make_review_record(index: int, segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_index": index,
        "segment_id": segment.get("segment_id"),
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
        "embedding_text": segment.get("embedding_text"),
        "semantic_status": "pending",
        "semantic_issue": "",
        "recommended_fix": "",
    }


def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    segments = read_jsonl(SEGMENTS)
    records = [make_review_record(index, segment) for index, segment in enumerate(segments, 1)]
    with (OUT_DIR / "semantic_review_sheet.jsonl").open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "total_segments": len(records),
        "category_counts": dict(Counter(record["table_category"] for record in records)),
        "role_counts": dict(Counter(record["segment_role"] for record in records)),
    }
    write_json(OUT_DIR / "summary.json", summary)
    write_markdown(OUT_DIR / "semantic_review_sheet.md", records, summary)
    write_category_dumps(OUT_DIR / "category_dump", records)
    return summary


def write_markdown(path: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 10 Segment 语义逐条审读工作表\n")
    lines.append(f"此文件用于人工/模型逐条审读 Step 09 输出的 {summary['total_segments']} 个低置信表修正 segment。`semantic_status` 的最终结论写入审计产物，不在此模板中预填。\n")
    lines.append("## 总览\n")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    lines.append(f"| `total_segments` | {summary['total_segments']} |")
    lines.append("\n## 待审条目\n")
    lines.append("| # | category | anchor | embedded | rows | role | context | subject/group | raw_text |")
    lines.append("|---:|---|---|---|---|---|---|---|---|")
    for record in records:
        subject_group = record.get("table_subject") or record.get("group")
        lines.append(
            f"| {record['review_index']} | {md(record['table_category'], 36)} | `{md(record['anchor'], 34)}` | "
            f"{md(record.get('embedded_file_name'), 34)} | {md(record['source_row_indices'], 24)} | `{md(record['segment_role'], 18)}` | "
            f"{md(record['context'], 42)} | {md(subject_group, 48)} | {md(record['raw_text'], 220)} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)


def write_category_dumps(out_dir: Path, records: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old_file in out_dir.glob("*.md"):
        old_file.unlink()
    by_category: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_category.setdefault(record.get("table_category") or "unknown", []).append(record)
    for category, items in by_category.items():
        lines: list[str] = [f"# {category}\n", f"count={len(items)}\n"]
        for index, record in enumerate(items, 1):
            lines.append(
                f"## {index}. {record.get('file_name')} | {record.get('anchor')} | "
                f"embedded={record.get('embedded_file_name') or ''} | rows={record.get('source_row_indices')} | "
                f"role={record.get('segment_role')}"
            )
            lines.append(f"context: {record.get('context') or ''}\n")
            if record.get("table_subject"):
                lines.append(f"subject: {record.get('table_subject')}\n")
            if record.get("group"):
                lines.append(f"group: {record.get('group')}\n")
            lines.append((record.get("raw_text") or "") + "\n")
        (out_dir / f"{safe_filename(category)}.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    summary = run()
    print(f"built semantic review sheet for {summary['total_segments']} segments -> {OUT_DIR}")
