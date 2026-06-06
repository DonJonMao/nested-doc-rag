from nested_doc_rag.excel.comments import format_source_comment
from nested_doc_rag.excel.writeback import prepare_writeback_item


def test_prepare_writeback_item() -> None:
    item = prepare_writeback_item("Sheet1", "A1", "未找到", comment="source missing")
    assert item.sheet_name == "Sheet1"
    assert item.cell == "A1"
    assert item.value == "未找到"
    assert item.comment == "source missing"


def test_format_source_comment() -> None:
    assert format_source_comment("chunk_1") == "chunk_1"
    assert format_source_comment("chunk_1", "low confidence") == "chunk_1\nlow confidence"
