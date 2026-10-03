from __future__ import annotations

from copy import deepcopy

from nested_doc_rag.cli import build_parser, step15_agentic_cli_overrides
from nested_doc_rag.config import load_app_config
from nested_doc_rag.evidence_resolver import resolve_evidence_refs
from nested_doc_rag.retrieval.layered import select_field_families


def schema(family: str, namespace: str = "room") -> dict:
    return {"schema_id": f"schema-{family}", "retrieval_object": "field_schema", "namespace": namespace,
            "field_family_id": family, "text_for_embedding": family}


class Schemas:
    def __init__(self, packs: dict[str, list[dict]]):
        self.packs = packs
        self.queries = []

    def search_field_schemas(self, query, *, namespaces, top_k):
        self.queries.append((query, namespaces, top_k))
        return deepcopy(self.packs.get(query, []))


class MatchingReranker:
    def rerank(self, query, docs, *, top_n):
        assert top_n == 1
        return [{"index": docs.index(query), "relevance_score": 1}] if query in docs else []


def test_required_facts_each_select_one_family_and_duplicate_queries_are_reused() -> None:
    retriever = Schemas({"UPS品牌": [schema("油机品牌"), schema("UPS品牌")],
                         "UPS容量": [schema("油机容量"), schema("UPS容量")]})
    selected = select_field_families(["UPS品牌", "UPS容量", "UPS容量"], retriever=retriever,
                                     reranker=MatchingReranker(), namespace="room", top_k=16)
    assert selected["field_family_ids"] == ["UPS品牌", "UPS容量"]
    assert len(retriever.queries) == 2 and selected["fallback"] is None
    assert [entry["selected_schema_ids"] for entry in selected["queries"]] == [["schema-UPS品牌"], ["schema-UPS容量"]]


def test_only_a_pack_without_any_schema_can_use_the_legacy_fallback() -> None:
    retriever = Schemas({"missing": [schema("other")]})
    selected = select_field_families(["missing"], retriever=retriever, reranker=MatchingReranker(), namespace="room", top_k=16)
    assert selected["field_family_ids"] == [] and selected["fallback"] is None
    assert selected["queries"][0]["diagnostic"] == "invalid_schema_selection"
    absent = select_field_families(["absent"], retriever=retriever, reranker=MatchingReranker(), namespace="room", top_k=16)
    assert absent["field_family_ids"] is None and absent["fallback"] == "no_schema"


def test_a_missing_slot_does_not_broaden_other_slots_or_allow_foreign_schema() -> None:
    retriever = Schemas({"UPS容量": [schema("UPS容量")], "油机容量": [schema("油机容量", "other-room")]})
    selected = select_field_families(["UPS容量", "absent", "油机容量"], retriever=retriever,
                                     reranker=MatchingReranker(), namespace="room", top_k=16)
    assert selected["field_family_ids"] == ["UPS容量"] and selected["fallback"] is None


def test_auxiliary_schema_cannot_be_certified_even_if_it_forges_native_evidence_fields() -> None:
    forged = {**schema("UPS容量"), "chunk_id": "schema-ref", "evidence_kind": "structured_field",
              "file_name": "source.xlsx", "sheet_name": "能力", "row_index": 2, "cell_range": "A2:B2",
              "raw_source_text": "UPS容量：500kVA"}
    resolution = resolve_evidence_refs(["schema-ref"], [forged])
    assert not resolution.resolvable
    assert any(error["reason"] == "auxiliary_schema_is_not_source_evidence" for error in resolution.errors)


def test_a4_is_an_independent_opt_in_and_legacy_mas_keeps_that_choice(tmp_path) -> None:
    config = load_app_config(project_root=tmp_path, default_config=tmp_path / "absent.yaml", env={})
    assert config.retrieval.schema_first_enabled is False and config.retrieval.sufficiency_enabled is True
    args = build_parser().parse_args(["run-step15-agent", "--out-dir", str(tmp_path), "--schema-first-enabled", "--agentic-mas"])
    assert step15_agentic_cli_overrides(args)["retrieval"] == {"schema_first_enabled": True, "sufficiency_enabled": False}
