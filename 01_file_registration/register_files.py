from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_DATA_DIR = Path("/Users/mao/projects/datacenter/data")
DEFAULT_OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/01_file_registration")

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".docx", ".doc", ".pdf", ".png", ".jpg", ".jpeg"}


def stable_file_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:10]
    return f"file_{digest}"


def infer_document_role(path: Path, data_dir: Path) -> str:
    rel_parts = path.relative_to(data_dir).parts
    name = path.name
    suffix = path.suffix.lower()

    if "工勘单" in rel_parts:
        return "survey_form"
    if suffix in {".docx", ".doc"} and "情况说明介绍" in name:
        return "intro_doc"
    if suffix in {".xlsx", ".xls"}:
        return "knowledge_base"
    if suffix in {".png", ".jpg", ".jpeg", ".pdf"}:
        return "proof_attachment"
    return "unknown"


def should_register(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.startswith("."):
        return False
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return False
    return True


def make_record(path: Path, data_dir: Path) -> dict[str, Any]:
    rel = path.relative_to(data_dir).as_posix()
    stat = path.stat()
    return {
        "file_id": stable_file_id(rel),
        "file_name": path.name,
        "declared_ext": path.suffix.lower(),
        "document_role": infer_document_role(path, data_dir),
        "source_path": str(path),
        "relative_path": rel,
        "parent_file_id": None,
        "level": 0,
        "size_bytes": stat.st_size,
        "size_mb": round(stat.st_size / 1024 / 1024, 3),
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_visualization(out_dir: Path, records: list[dict[str, Any]], data_dir: Path) -> None:
    role_counts = Counter(r["document_role"] for r in records)
    ext_counts = Counter(r["declared_ext"] for r in records)

    lines: list[str] = []
    lines.append("# Step 01 文件登记可视化\n")
    lines.append(f"- 数据目录：`{data_dir}`")
    lines.append(f"- 登记文件数：**{len(records)}**")
    lines.append("- 已忽略隐藏文件和系统文件，例如 `.DS_Store`。\n")

    lines.append("## 角色统计\n")
    lines.append("| document_role | count |")
    lines.append("|---|---:|")
    for role, count in sorted(role_counts.items()):
        lines.append(f"| `{role}` | {count} |")

    lines.append("\n## 扩展名统计\n")
    lines.append("| ext | count |")
    lines.append("|---|---:|")
    for ext, count in sorted(ext_counts.items()):
        lines.append(f"| `{ext}` | {count} |")

    lines.append("\n## 文件清单\n")
    lines.append("| file_id | role | size_mb | relative_path |")
    lines.append("|---|---|---:|---|")
    for record in records:
        lines.append(
            f"| `{record['file_id']}` | `{record['document_role']}` | "
            f"{record['size_mb']} | `{record['relative_path']}` |"
        )

    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(data_dir: Path = DEFAULT_DATA_DIR, out_dir: Path = DEFAULT_OUT_DIR) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [make_record(path, data_dir) for path in sorted(data_dir.rglob("*")) if should_register(path)]

    write_jsonl(out_dir / "file_manifest.jsonl", records)
    summary = {
        "data_dir": str(data_dir),
        "total_registered_files": len(records),
        "role_counts": dict(Counter(r["document_role"] for r in records)),
        "extension_counts": dict(Counter(r["declared_ext"] for r in records)),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_visualization(out_dir, records, data_dir)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 01: register source files.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    records = run(args.data_dir, args.out_dir)
    print(f"registered {len(records)} files -> {args.out_dir}")


if __name__ == "__main__":
    main()
