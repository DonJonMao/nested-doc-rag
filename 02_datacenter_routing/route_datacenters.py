from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_IN = Path("/Users/mao/projects/datacenter/artifacts/01_file_registration/file_manifest.jsonl")
DEFAULT_OUT_DIR = Path("/Users/mao/projects/datacenter/artifacts/02_datacenter_routing")

XIXIAN_IDS = [f"xixian_{i}" for i in range(1, 7)]

DATACENTER_ALIASES: dict[str, list[str]] = {
    "xixian_1": ["西咸数据中心1号楼", "西咸1号楼", "1号楼"],
    "xixian_2": ["西咸数据中心2号楼", "西咸2号楼", "2号楼"],
    "xixian_3": ["西咸数据中心3号楼", "西咸3号楼", "3号楼"],
    "xixian_4": ["西咸数据中心4号楼", "西咸4号楼", "4号楼"],
    "xixian_5": ["西咸数据中心5号楼", "西咸5号楼", "5号楼"],
    "xixian_6": ["西咸数据中心6号楼", "西咸6号楼", "6号楼"],
    "xian": ["陕西西安", "西安数据中心", "锦业路", "陕西西安移动"],
    "chengdong_baqiao": ["城东数据中心", "灞桥", "港务区", "港务大道"],
    "xianyang": ["陕西咸阳", "咸阳数据中心", "咸阳"],
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def route_record(record: dict[str, Any]) -> dict[str, Any]:
    text = f"{record['relative_path']} {record['file_name']}"
    role = record["document_role"]

    exact_candidates: list[dict[str, Any]] = []

    # Prefer explicit xixian building mentions. This avoids treating every "1号楼"
    # in generic text as xixian_1 unless "西咸" is also present.
    for number in range(1, 7):
        patterns = [
            rf"西咸数据中心{number}号楼",
            rf"西咸.*{number}号楼",
            rf"西咸{number}号楼",
        ]
        if any(re.search(pattern, text) for pattern in patterns):
            exact_candidates.append(
                {
                    "data_center_id": f"xixian_{number}",
                    "matched_alias": f"西咸数据中心{number}号楼",
                    "confidence": 0.99,
                    "route_source": "file_name",
                }
            )

    if not exact_candidates:
        for data_center_id, aliases in DATACENTER_ALIASES.items():
            if data_center_id.startswith("xixian_"):
                continue
            for alias in aliases:
                if alias in text:
                    confidence = 0.95
                    route_status = "exact"
                    if role == "survey_form":
                        confidence = 0.62
                        route_status = "needs_review"
                    return {
                        **record,
                        "data_center_id": data_center_id if route_status == "exact" else None,
                        "route_status": route_status,
                        "route_candidates": [
                            {
                                "data_center_id": data_center_id,
                                "matched_alias": alias,
                                "confidence": confidence,
                                "route_source": "file_name",
                            }
                        ],
                    }

    if len(exact_candidates) == 1:
        return {
            **record,
            "data_center_id": exact_candidates[0]["data_center_id"],
            "route_status": "exact",
            "route_candidates": exact_candidates,
        }

    if "西咸" in text:
        candidates = [
            {
                "data_center_id": data_center_id,
                "matched_alias": "陕西西咸",
                "confidence": 0.5,
                "route_source": "file_name",
            }
            for data_center_id in XIXIAN_IDS
        ]
        return {**record, "data_center_id": None, "route_status": "ambiguous", "route_candidates": candidates}

    if record["file_name"] == "陕西移动IDC对外服务知识库.xlsx":
        return {**record, "data_center_id": None, "route_status": "global", "route_candidates": []}

    return {**record, "data_center_id": None, "route_status": "unrouted", "route_candidates": []}


def write_visualization(out_dir: Path, records: list[dict[str, Any]]) -> None:
    status_counts = Counter(r["route_status"] for r in records)
    exact_counts = Counter(r["data_center_id"] for r in records if r.get("data_center_id"))

    lines: list[str] = []
    lines.append("# Step 02 数据中心路由可视化\n")
    lines.append(f"- 输入文件数：**{len(records)}**")
    lines.append("- 本步骤只使用文件名和路径；不读取 Excel/Word 内容。")
    lines.append("- 不确定项保留为 `ambiguous` / `needs_review` / `unrouted`，后续 4A/4B 再修正。\n")

    lines.append("## 路由状态统计\n")
    lines.append("| route_status | count |")
    lines.append("|---|---:|")
    for status, count in sorted(status_counts.items()):
        lines.append(f"| `{status}` | {count} |")

    lines.append("\n## 已明确路由的库\n")
    lines.append("| data_center_id | count |")
    lines.append("|---|---:|")
    for data_center_id, count in sorted(exact_counts.items()):
        lines.append(f"| `{data_center_id}` | {count} |")

    lines.append("\n## 文件路由明细\n")
    lines.append("| file | role | route_status | data_center_id | candidates |")
    lines.append("|---|---|---|---|---|")
    for record in records:
        candidates = ", ".join(
            f"{c['data_center_id']}({c['confidence']})" for c in record.get("route_candidates", [])[:6]
        )
        lines.append(
            f"| `{record['relative_path']}` | `{record['document_role']}` | "
            f"`{record['route_status']}` | `{record.get('data_center_id') or ''}` | {candidates} |"
        )

    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(input_path: Path = DEFAULT_IN, out_dir: Path = DEFAULT_OUT_DIR) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [route_record(record) for record in read_jsonl(input_path)]
    write_jsonl(out_dir / "routed_manifest.jsonl", records)
    summary = {
        "total_files": len(records),
        "route_status_counts": dict(Counter(r["route_status"] for r in records)),
        "exact_data_center_counts": dict(Counter(r["data_center_id"] for r in records if r.get("data_center_id"))),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_visualization(out_dir, records)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 02: route files to datacenter partitions.")
    parser.add_argument("--input", type=Path, default=DEFAULT_IN)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    records = run(args.input, args.out_dir)
    print(f"routed {len(records)} files -> {args.out_dir}")


if __name__ == "__main__":
    main()
