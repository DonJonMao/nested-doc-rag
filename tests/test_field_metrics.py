from nested_doc_rag.evaluation.field_metrics import exact_match


def test_exact_match_normalizes_whitespace() -> None:
    assert exact_match("西咸4号楼 301机房", "西咸4号楼   301机房")
    assert not exact_match("301", "302")
