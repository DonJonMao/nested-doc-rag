from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path

from openpyxl import Workbook

from nested_doc_rag.embedding.manifest import build_manifest, read_jsonl

TINY_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_build_manifest_materializes_proof_attachment_registry(tmp_path: Path) -> None:
    source_root = tmp_path / "data"
    source_root.mkdir()
    source = source_root / "能力清单.xlsx"
    workbook = Workbook()
    workbook.save(source)
    with zipfile.ZipFile(source, "a") as archive:
        archive.writestr("xl/media/proof.png", base64.b64decode(TINY_PNG))

    segments_path = tmp_path / "segments.jsonl"
    resolved_segments_path = tmp_path / "resolved.jsonl"
    audit_path = tmp_path / "audit.jsonl"
    write_jsonl(
        segments_path,
        [
            {
                "segment_id": "seg_1",
                "data_center_id": "xixian_4",
                "file_id": "file_1",
                "file_name": "能力清单.xlsx",
                "relative_path": "能力清单.xlsx",
                "sheet_name": "能力清单",
                "row_index": 2,
                "source_anchor": {"sheet_name": "能力清单", "cell_range": "A2:C2", "proof_cells": ["C2"]},
                "raw_text": "机房名称 / 西咸4号楼",
                "proof_attachments": [
                    {
                        "attachment_id": "att_file_1_C2_dispimg",
                        "file_id": "file_1",
                        "sheet_name": "能力清单",
                        "source_cell": "C2",
                        "media_path": "xl/media/proof.png",
                        "media_content_type": "image/png",
                        "attachment_type": "image",
                    }
                ],
            }
        ],
    )
    write_jsonl(resolved_segments_path, [])
    write_jsonl(audit_path, [])

    summary = build_manifest(
        tmp_path / "out",
        segments_path=segments_path,
        resolved_segments_path=resolved_segments_path,
        semantic_audit_path=audit_path,
        source_root=source_root,
    )

    assert summary["proof_attachment_count"] == 1
    assert summary["materialized_image_count"] == 1
    registry = read_jsonl(tmp_path / "out" / "proof_attachment_registry.jsonl")
    assert Path(registry[0]["image_path"]).exists()
    records = read_jsonl(tmp_path / "out" / "ingestion_manifest.jsonl")
    assert records[0]["proof_attachments"][0]["image_path"] == registry[0]["image_path"]
