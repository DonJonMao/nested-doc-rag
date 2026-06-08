from __future__ import annotations

import pytest

from nested_doc_rag.llm import JsonRepairError, extract_json_object


def test_extract_valid_json() -> None:
    assert extract_json_object('{"answer_status": "answered", "confidence": 0.8}') == {
        "answer_status": "answered",
        "confidence": 0.8,
    }


def test_extract_fenced_json() -> None:
    text = '```json\n{"answer_status": "partial_clue"}\n```'

    assert extract_json_object(text)["answer_status"] == "partial_clue"


def test_extract_prefix_suffix_json() -> None:
    text = '模型说明：{"answer_status": "not_found", "source_chunk_ids": []}谢谢'

    assert extract_json_object(text)["answer_status"] == "not_found"


def test_extract_trailing_comma_json() -> None:
    text = '{"answer_status": "answered", "source_chunk_ids": ["a",],}'

    assert extract_json_object(text) == {"answer_status": "answered", "source_chunk_ids": ["a"]}


def test_invalid_non_json_raises_clear_error() -> None:
    with pytest.raises(JsonRepairError, match="could not extract JSON object"):
        extract_json_object("没有 JSON")
