from __future__ import annotations

from pathlib import Path

from nested_doc_rag.retrieval.lexical import BM25Index, tokenize


def test_bm25_tokenizer_handles_equipment_terms() -> None:
    tokens = tokenize("UPS 500kVA A/B路 PDU 机柜 10G 中文字段 U位 4*10G")

    assert "ups" in tokens
    assert "500kva" in tokens
    assert "500" in tokens
    assert "kva" in tokens
    assert "a/b" in tokens
    assert "pdu" in tokens
    assert "机柜" in tokens
    assert "10g" in tokens
    assert "中文" in tokens
    assert "文字" in tokens or "字段" in tokens
    assert "u位" in tokens
    assert "4*10g" in tokens


def test_bm25_tokenizer_chinese_not_single_sentence_token() -> None:
    tokens = tokenize("供配电系统包含UPS和PDU机柜容量")

    assert "供配电系统包含ups和pdu机柜容量" not in tokens
    assert "机柜" in tokens
    assert any(len(token) in {2, 3} and "供" in token for token in tokens)


def test_bm25_retrieval_matches_lexical_terms_and_filters(tmp_path: Path) -> None:
    index = BM25Index.from_records(
        [
            record("ups_500", "xixian_4", "UPS容量：500kVA，PDU接入。"),
            record("ups_300", "xixian_6", "UPS容量：300kVA。"),
            record("cabinet", "xixian_4", "机柜数量：20台。"),
        ]
    )

    ups_hits = index.search("UPS", namespaces=["xixian_4"], layers=["fact"], source_types=["main_excel_capability"], top_k=5)
    capacity_hits = index.search("500kVA", namespaces=["xixian_4"], layers=["fact"], source_types=["main_excel_capability"], top_k=5)
    cross_namespace_hits = index.search("300kVA", namespaces=["xixian_4"], layers=["fact"], source_types=["main_excel_capability"], top_k=5)

    assert ups_hits[0]["chunk_id"] == "ups_500"
    assert capacity_hits[0]["chunk_id"] == "ups_500"
    assert cross_namespace_hits == []

    path = tmp_path / "lexical_index.json"
    index.save(path)
    loaded = BM25Index.load(path)
    assert loaded.search("PDU", namespaces=["xixian_4"], layers=["fact"], top_k=1)[0]["chunk_id"] == "ups_500"


def record(chunk_id: str, namespace: str, raw_text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "namespace": namespace,
        "corpus_layer": "fact",
        "source_type": "main_excel_capability",
        "text_for_embedding": raw_text,
        "raw_text": raw_text,
        "source_document": "main.xlsx",
    }
