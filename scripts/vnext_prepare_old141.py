#!/usr/bin/env python3
"""Prepare read-only old141 inputs; heldout facts never enter closed-book items."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

OLD_FILE = "基地云机房信息调研表.xlsx"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blank_targets(source: Path, output: Path, targets: dict[str, set[str]]) -> dict[str, int]:
    """Edit only selected native cell values; retain all other ZIP members.

    No workbook reauthoring, recalculation or style changes. Formula cells are
    preserved, even if an old form-item record incorrectly marks them writable.
    """
    if source.resolve() == output.resolve() or output.exists():
        raise ValueError("blank workbook output must be a new file distinct from the source")
    with ZipFile(source) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        by_id = {row.attrib["Id"]: row.attrib["Target"] for row in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")}
        parts: dict[str, set[str]] = {}
        for sheet in workbook.findall(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet"):
            name = sheet.attrib["name"]
            if name in targets:
                part = by_id[sheet.attrib[f"{{{REL_NS}}}id"]]
                path = part.lstrip("/") if part.startswith("/") else posixpath.normpath(posixpath.join("xl", part))
                parts[path] = targets[name]
        if sum(len(cells) for cells in parts.values()) != sum(len(cells) for cells in targets.values()):
            raise ValueError("a requested target sheet is absent from the workbook")
        changed, formulas = 0, 0
        with ZipFile(output, "w") as result:
            for item in archive.infolist():
                data = archive.read(item.filename)
                if item.filename in parts:
                    root = ET.fromstring(data)
                    seen = set()
                    for cell in root.iter(f"{{{MAIN_NS}}}c"):
                        address = cell.attrib.get("r")
                        if address not in parts[item.filename]:
                            continue
                        seen.add(address)
                        if cell.find(f"{{{MAIN_NS}}}f") is not None:
                            formulas += 1
                            continue
                        for child in list(cell):
                            if child.tag in {f"{{{MAIN_NS}}}v", f"{{{MAIN_NS}}}is"}:
                                cell.remove(child)
                        cell.attrib.pop("t", None)
                        changed += 1
                    # An absent ordinary cell is already blank.
                    changed += len(parts[item.filename] - seen)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                result.writestr(item, data)
    return {"cleared_target_count": changed, "preserved_formula_count": formulas}


def prepare(original_root: Path, out_dir: Path) -> dict[str, object]:
    original_root = original_root.resolve()
    if out_dir.resolve().is_relative_to(original_root):
        raise ValueError("output must be outside the original repository")
    form_path = original_root / "artifacts/12_gongkan_form_analysis/form_items.jsonl"
    source = original_root / "data/工勘单" / OLD_FILE
    before = {str(path): sha256(path) for path in (form_path, source)}
    records = [json.loads(line) for line in form_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = [row for row in records if row.get("file_name") == OLD_FILE and 4 <= int(row.get("row_index", 0)) <= 144]
    if len(selected) != 141 or len({row["row_index"] for row in selected}) != 141:
        raise ValueError("old141 requires exactly one field for each row 4..144")
    out_dir.mkdir(parents=True, exist_ok=False)
    allowed = ("form_item_id", "file_name", "sheet_name", "row_index", "target_cell", "category_path",
               "question_text", "instruction_text", "needs_evidence")
    closed = [{**{key: row[key] for key in allowed if key in row}, "answer_example": ""} for row in selected]
    heldout = [{"field_id": row["form_item_id"], "row_index": row["row_index"], "target_cell": row["target_cell"], "sheet_name": row["sheet_name"],
                "expected_value": row.get("existing_value"), "gold_origin": "legacy_heldout_unverified", "gold_verified": False}
               for row in selected]
    outputs = {"form_items_closed_book.jsonl": closed, "form_items_legacy_replay.jsonl": selected,
               "heldout_answers.jsonl": heldout}
    for name, rows in outputs.items():
        (out_dir / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    targets: dict[str, set[str]] = {}
    for row in selected:
        if "!" in row["target_cell"]:
            sheet, cell = row["target_cell"].rsplit("!", 1)
        else:
            sheet, cell = row["sheet_name"], row["target_cell"]
        sheet = sheet.strip("'").replace("''", "'")
        targets.setdefault(sheet, set()).add(cell.replace("$", "").upper())
    blank = out_dir / "blank_target_template.xlsx"
    counts = blank_targets(source, blank, targets)
    after = {str(path): sha256(path) for path in (form_path, source)}
    if before != after:
        raise RuntimeError("original input bytes changed during preparation")
    report = {"schema_version": "old141-preparation-v1", "original_root": str(original_root), "field_count": 141,
              "source_hashes": before, "original_input_unchanged": True, **counts,
              "fact_example_equal_to_heldout_count": sum(bool(row.get("answer_example")) and row.get("answer_example") == row.get("existing_value") for row in selected),
              "generated_hashes": {path.name: sha256(path) for path in sorted(out_dir.iterdir()) if path.is_file()},
              "gold_status": "heldout answers are an unverified human-review starting point, not independent evidence gold",
              "closed_book_rule": "only whitelisted field text and empty format example; no existing/current/heldout/status/query facts",
              "workbook_rule": "only target values blanked; formulas and all untouched XLSX package parts preserved",
              "model_requests": 0}
    (out_dir / "preparation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, default=Path("/Users/mao/projects/datacenter"))
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.original_root, args.out_dir)
    print(json.dumps({"field_count": report["field_count"], "cleared_target_count": report["cleared_target_count"],
                      "original_input_unchanged": report["original_input_unchanged"], "model_requests": 0}))


if __name__ == "__main__":
    main()
