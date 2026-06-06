from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/02_datacenter_routing"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def by_relative_path(records: list[dict]) -> dict[str, dict]:
    return {record["relative_path"]: record for record in records}


def test_outputs_exist() -> None:
    assert (OUT_DIR / "routed_manifest.jsonl").exists()
    assert (OUT_DIR / "summary.json").exists()
    assert (OUT_DIR / "visualization.md").exists()


def test_exact_kb_routes() -> None:
    records = by_relative_path(read_jsonl(OUT_DIR / "routed_manifest.jsonl"))
    assert records["西咸数据中心2号楼维护能力知识库.xlsx"]["data_center_id"] == "xixian_2"
    assert records["西咸数据中心6号楼维护能力知识库.xlsx"]["data_center_id"] == "xixian_6"
    assert records["中国移动（陕西西安）数据中心机房维护能力知识库.xlsx"]["data_center_id"] == "xian"
    assert records["城东数据中心维护能力知识库.xlsx"]["data_center_id"] == "chengdong_baqiao"
    assert records["中国移动（陕西咸阳）数据中心维护能力知识库 .xlsx"]["data_center_id"] == "xianyang"


def test_ambiguous_and_global_routes_are_not_forced() -> None:
    records = by_relative_path(read_jsonl(OUT_DIR / "routed_manifest.jsonl"))
    xixian_intro = records["中国移动（陕西西咸）数据中心机房情况说明介绍.docx"]
    assert xixian_intro["route_status"] == "ambiguous"
    assert xixian_intro["data_center_id"] is None
    assert len(xixian_intro["route_candidates"]) == 6

    global_kb = records["陕西移动IDC对外服务知识库.xlsx"]
    assert global_kb["route_status"] == "global"
    assert global_kb["data_center_id"] is None


if __name__ == "__main__":
    test_outputs_exist()
    test_exact_kb_routes()
    test_ambiguous_and_global_routes_are_not_forced()
    print("step 02 tests passed")
