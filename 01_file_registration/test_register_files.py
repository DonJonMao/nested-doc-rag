from __future__ import annotations

import json
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parents[1] / "artifacts/01_file_registration"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outputs_exist() -> None:
    assert (OUT_DIR / "file_manifest.jsonl").exists()
    assert (OUT_DIR / "summary.json").exists()
    assert (OUT_DIR / "visualization.md").exists()


def test_expected_file_counts() -> None:
    records = read_jsonl(OUT_DIR / "file_manifest.jsonl")
    assert len(records) == 19
    roles = {}
    for record in records:
        roles[record["document_role"]] = roles.get(record["document_role"], 0) + 1
    assert roles["knowledge_base"] == 10
    assert roles["survey_form"] == 5
    assert roles["intro_doc"] == 4


def test_manifest_has_required_fields() -> None:
    records = read_jsonl(OUT_DIR / "file_manifest.jsonl")
    required = {"file_id", "file_name", "declared_ext", "document_role", "source_path", "relative_path"}
    for record in records:
        assert required.issubset(record)
        assert not record["file_name"].startswith(".")


if __name__ == "__main__":
    test_outputs_exist()
    test_expected_file_counts()
    test_manifest_has_required_fields()
    print("step 01 tests passed")
