from __future__ import annotations

from nested_doc_rag.schemas.excel import ExcelWritebackItem


def prepare_writeback_item(sheet_name: str, cell: str, value: object, comment: str | None = None) -> ExcelWritebackItem:
    return ExcelWritebackItem(sheet_name=sheet_name, cell=cell, value=value, comment=comment)
