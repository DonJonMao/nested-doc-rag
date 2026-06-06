from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_IN = Path("/Users/mao/projects/datacenter/artifacts/02_datacenter_routing/routed_manifest.jsonl")
DEFAULT_OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/03_format_probe")

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def safe_zip_names(path: Path) -> tuple[bool, list[str], str | None]:
    try:
        if not zipfile.is_zipfile(path):
            return False, [], None
        with zipfile.ZipFile(path) as zf:
            return True, zf.namelist(), None
    except Exception as exc:  # noqa: BLE001 - diagnostics should preserve parser failures.
        return False, [], f"{type(exc).__name__}: {exc}"


def probe(record: dict[str, Any]) -> dict[str, Any]:
    path = Path(record["source_path"])
    diagnostics: list[str] = []

    if not path.exists():
        return {
            **record,
            "detected_package_type": "missing",
            "parser_type": "none",
            "parse_status": "missing",
            "fallback_action": "restore_source_file",
            "diagnostics": ["source path does not exist"],
        }

    data = path.read_bytes()[:64]
    is_ole = data.startswith(OLE_MAGIC)
    pk_offset = path.read_bytes().find(b"PK\x03\x04")
    is_zip, names, zip_error = safe_zip_names(path)
    name_set = set(names)

    if is_ole:
        diagnostics.append("ole compound document magic detected")
    if is_zip:
        diagnostics.append("zip central directory readable")
    if zip_error:
        diagnostics.append(f"zip error: {zip_error}")
    if pk_offset > 0:
        diagnostics.append(f"extra bytes before first zip local header: {pk_offset}")

    has_content_types = "[Content_Types].xml" in name_set
    has_workbook = "xl/workbook.xml" in name_set
    has_doc = "word/document.xml" in name_set
    has_cellimages = any(name.lower() == "xl/cellimages.xml" for name in name_set)
    media_count = sum(1 for name in names if name.startswith("xl/media/") or name.startswith("word/media/"))
    embedding_count = sum(1 for name in names if "/embeddings/" in name)

    if has_content_types:
        diagnostics.append("[Content_Types].xml present")
    if has_cellimages:
        diagnostics.append("WPS cellimages part present")

    if is_zip and has_workbook:
        detected = "xlsx_ooxml"
        parser_type = "xlsx_ooxml"
        parse_status = "ok"
        fallback_action = None
    elif is_zip and has_doc:
        detected = "docx_ooxml"
        parser_type = "docx_ooxml"
        parse_status = "ok"
        fallback_action = None
    elif is_zip:
        detected = "mixed_or_invalid_ooxml"
        parser_type = "package_probe_only"
        parse_status = "needs_conversion"
        fallback_action = "open_and_resave_with_wps_or_libreoffice"
        if not has_workbook and record["declared_ext"] in {".xlsx", ".xls"}:
            diagnostics.append("xl/workbook.xml missing")
        if not has_doc and record["declared_ext"] in {".docx", ".doc"}:
            diagnostics.append("word/document.xml missing")
    elif is_ole:
        detected = "ole_compound_document"
        parser_type = "ole_probe"
        parse_status = "needs_conversion"
        fallback_action = "convert_ole_document_to_ooxml"
    else:
        detected = "unknown_binary_or_text"
        parser_type = "none"
        parse_status = "unsupported"
        fallback_action = "manual_review"

    return {
        **record,
        "detected_package_type": detected,
        "parser_type": parser_type,
        "parse_status": parse_status,
        "fallback_action": fallback_action,
        "probe": {
            "is_zip": is_zip,
            "is_ole": is_ole,
            "has_content_types": has_content_types,
            "has_workbook": has_workbook,
            "has_word_document": has_doc,
            "has_cellimages": has_cellimages,
            "media_count": media_count,
            "embedding_count": embedding_count,
            "zip_part_count": len(names),
        },
        "diagnostics": diagnostics,
    }


def write_visualization(out_dir: Path, records: list[dict[str, Any]]) -> None:
    status_counts = Counter(r["parse_status"] for r in records)
    parser_counts = Counter(r["parser_type"] for r in records)

    lines: list[str] = []
    lines.append("# Step 03 文件格式探测可视化\n")
    lines.append(f"- 输入文件数：**{len(records)}**")
    lines.append("- 本步骤探测真实包结构，输出 `parser_type`、`parse_status` 和 `fallback_action`。\n")

    lines.append("## parse_status 统计\n")
    lines.append("| parse_status | count |")
    lines.append("|---|---:|")
    for status, count in sorted(status_counts.items()):
        lines.append(f"| `{status}` | {count} |")

    lines.append("\n## parser_type 统计\n")
    lines.append("| parser_type | count |")
    lines.append("|---|---:|")
    for parser, count in sorted(parser_counts.items()):
        lines.append(f"| `{parser}` | {count} |")

    lines.append("\n## 文件探测明细\n")
    lines.append("| file | declared_ext | parser_type | parse_status | media | embeddings | diagnostics |")
    lines.append("|---|---|---|---|---:|---:|---|")
    for record in records:
        diagnostics = "; ".join(record.get("diagnostics", [])[:4])
        probe_info = record.get("probe", {})
        lines.append(
            f"| `{record['relative_path']}` | `{record['declared_ext']}` | `{record['parser_type']}` | "
            f"`{record['parse_status']}` | {probe_info.get('media_count', 0)} | "
            f"{probe_info.get('embedding_count', 0)} | {diagnostics} |"
        )

    needs = [record for record in records if record["parse_status"] != "ok"]
    if needs:
        lines.append("\n## 需要处理的异常文件\n")
        lines.append("| file | parse_status | fallback_action |")
        lines.append("|---|---|---|")
        for record in needs:
            lines.append(
                f"| `{record['relative_path']}` | `{record['parse_status']}` | "
                f"`{record.get('fallback_action') or ''}` |"
            )

    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(input_path: Path = DEFAULT_IN, out_dir: Path = DEFAULT_OUT_DIR) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [probe(record) for record in read_jsonl(input_path)]
    write_jsonl(out_dir / "probed_manifest.jsonl", records)
    summary = {
        "total_files": len(records),
        "parse_status_counts": dict(Counter(r["parse_status"] for r in records)),
        "parser_type_counts": dict(Counter(r["parser_type"] for r in records)),
        "needs_conversion_files": [r["relative_path"] for r in records if r["parse_status"] == "needs_conversion"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_visualization(out_dir, records)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 03: probe true file formats.")
    parser.add_argument("--input", type=Path, default=DEFAULT_IN)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    records = run(args.input, args.out_dir)
    print(f"probed {len(records)} files -> {args.out_dir}")


if __name__ == "__main__":
    main()
