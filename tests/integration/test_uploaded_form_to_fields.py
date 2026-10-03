from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from nested_doc_rag import cli
from nested_doc_rag.config import load_app_config


def test_uploaded_form_drives_form_items_instead_of_historical_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observe the real CLI's runtime fields, without requiring a new parser API."""
    template = tmp_path / "新上传_动力工勘.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "供电调研"
    worksheet.append(["类别", "指标名称", "填写说明", "实际情况", "备注"])
    worksheet.append(["供配电", "市电路数", "填写当前现网市电路数", None, "保留人工备注"])
    workbook.save(template)
    workbook.close()

    # Isolate the CLI from both the repository's historical data and .env.
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "missing.yaml", env={})
    config.paths.qdrant_path.mkdir(parents=True)
    historical_items = config.paths.artifacts_dir / "12_gongkan_form_analysis/form_items.jsonl"
    assert not historical_items.exists()
    observed: list[dict[str, Any]] = []

    class ObservingRunner:
        run_id = "phase0_observing_runner"
        writeback_status = "not_executed"
        mas_mode = "off"

        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["template_path"] == template

        def run(self, items: list[dict[str, Any]]) -> list[Any]:
            observed.extend(dict(item) for item in items)
            return []

    monkeypatch.setattr(cli, "load_app_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cli, "Step15AgentRunner", ObservingRunner)
    cli.main(
        [
            "run-step15-agent",
            "--template", str(template),
            "--writeback",
            "--target-namespace", "phase0_room_301",
            "--global-namespace", "phase0_global",
            "--rows", "all",
            "--out-dir", str(tmp_path / "run"),
            "--no-judge",
        ]
    )

    assert len(observed) == 1, (
        "The uploaded template must produce this run's field list without historical form_items.jsonl; "
        f"the actual CLI passed {len(observed)} fields to the runner"
    )
    field = observed[0]
    assert field["file_name"] == template.name
    assert field["sheet_name"] == "供电调研"
    assert field["row_index"] == 2
    assert field["question_text"] == "市电路数"
    assert field["target_cell"].split("!")[-1] == "D2"
    assert not historical_items.exists(), "Dynamic parsing must not recreate a shared historical artifact"
