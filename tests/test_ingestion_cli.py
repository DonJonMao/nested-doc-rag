from __future__ import annotations

import base64
import zipfile
from pathlib import Path

from openpyxl import Workbook

from nested_doc_rag.cli import build_parser
from nested_doc_rag.ingestion import build_ingestion_records, build_run_manifest, build_summary

TINY_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="


def test_ingest_knowledge_parser_accepts_worker_args() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "ingest-knowledge",
            "--config",
            "config/local.yaml",
            "--input-dir",
            "/tmp/input",
            "--namespace",
            "xixian_4",
            "--knowledge-base-id",
            "kb-1",
            "--out-dir",
            "/tmp/out",
            "--qdrant-collection",
            "datacenter_chunks_v1",
            "--qdrant-namespace",
            "xixian_4",
            "--resume",
        ]
    )

    assert args.command == "ingest-knowledge"
    assert args.namespace == "xixian_4"
    assert args.qdrant_collection == "datacenter_chunks_v1"
    assert args.qdrant_namespace == "xixian_4"
    assert args.resume is True


def test_build_ingestion_records_from_uploaded_excel(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "能力清单"
    sheet.append(["字段", "答案"])
    sheet.append(["机房名称", "西咸4号楼"])
    path = tmp_path / "能力清单.xlsx"
    workbook.save(path)

    records, skipped = build_ingestion_records(tmp_path, namespace="xixian_4", knowledge_base_id="kb-1")

    assert skipped == []
    assert len(records) == 2
    assert records[0]["namespace"] == "xixian_4"
    assert records[0]["source_type"] == "uploaded_excel_row"
    assert records[0]["point_id"]
    assert "能力清单.xlsx" in records[1]["text_for_embedding"]
    assert "西咸4号楼" in records[1]["raw_text"]


def test_build_ingestion_records_materializes_dispimg_registry(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "能力清单"
    sheet.append(["字段", "答案", "图片"])
    sheet.append(["机房名称", "西咸4号楼", '=_xlfn.DISPIMG("ID_TEST_IMAGE",1)'])
    path = tmp_path / "能力清单.xlsx"
    workbook.save(path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("xl/media/proof.png", base64.b64decode(TINY_PNG))
        archive.writestr(
            "xl/cellimages.xml",
            """
            <etc:cellImages xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:etc="http://www.wps.cn/officeDocument/2017/etCustomData">
              <etc:cellImage>
                <xdr:pic>
                  <xdr:nvPicPr><xdr:cNvPr id="1" name="ID_TEST_IMAGE"/></xdr:nvPicPr>
                  <xdr:blipFill><a:blip r:embed="rId1"/></xdr:blipFill>
                </xdr:pic>
              </etc:cellImage>
            </etc:cellImages>
            """,
        )
        archive.writestr(
            "xl/_rels/cellimages.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/proof.png"/>
            </Relationships>
            """,
        )

    records, skipped = build_ingestion_records(
        tmp_path,
        namespace="xixian_4",
        knowledge_base_id="kb-1",
        image_output_dir=tmp_path / "evidence_images",
    )

    assert skipped == []
    row_record = next(record for record in records if "西咸4号楼" in record["raw_text"])
    assert row_record["proof_attachment_ids"]
    assert row_record["proof_attachments"][0]["source_cell"] == "C2"
    assert Path(row_record["proof_attachments"][0]["image_path"]).exists()
    assert "DISPIMG" not in row_record["raw_text"]


def test_ingestion_summary_and_manifest_contract(tmp_path: Path) -> None:
    records = [
        {
            "source_type": "uploaded_text_chunk",
            "corpus_layer": "fact",
            "relative_path": "doc.txt",
        }
    ]

    summary = build_summary(
        records=records,
        skipped=[],
        collection_name="datacenter_chunks_v1",
        qdrant_path=tmp_path / "qdrant",
        qdrant_url="http://localhost:6333",
        namespace="xixian_4",
        embedding_endpoint="http://embedding",
        embedding_model="model",
        dimension=1024,
        upserted=1,
        elapsed_seconds=0.1,
    )
    manifest = build_run_manifest(
        summary=summary,
        namespace="xixian_4",
        knowledge_base_id="kb-1",
        artifacts={
            "ingestion_manifest": "ingestion_manifest.jsonl",
            "summary": "summary.json",
            "run_summary": "run_summary.md",
        },
    )

    assert summary["status"] == "completed"
    assert summary["record_count"] == 1
    assert summary["qdrant_url"] == "http://localhost:6333"
    assert manifest["status"] == "completed"
    assert manifest["engine"] == "gongkan_knowledge_ingestion"
    assert manifest["target_namespace"] == "xixian_4"
    assert manifest["artifacts"]["ingestion_manifest"] == "ingestion_manifest.jsonl"
