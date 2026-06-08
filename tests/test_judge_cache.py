from __future__ import annotations

from pathlib import Path

from nested_doc_rag.agent.step15_runner import append_judge_cache_record, build_judge_cache_key, load_judge_cache


def test_same_key_hit_reuses_result(tmp_path: Path) -> None:
    path = tmp_path / "judge_cache.jsonl"
    key = build_judge_cache_key(
        item=make_item(),
        generated=make_generated("2路市电"),
        heldout_answer="2路市电",
        judge_prompt_version="gongkan_eval_v1",
        judge_model="judge-model",
    )
    append_judge_cache_record(path, {"cache_key": key, "judge": {"label": "exact", "score": 1}})

    cache = load_judge_cache(path)

    assert cache[key]["label"] == "exact"


def test_different_answer_value_misses() -> None:
    key1 = build_judge_cache_key(
        item=make_item(),
        generated=make_generated("2路市电"),
        heldout_answer="2路市电",
        judge_prompt_version="gongkan_eval_v1",
        judge_model="judge-model",
    )
    key2 = build_judge_cache_key(
        item=make_item(),
        generated=make_generated("1路市电"),
        heldout_answer="2路市电",
        judge_prompt_version="gongkan_eval_v1",
        judge_model="judge-model",
    )

    assert key1 != key2


def test_overlay_changes_do_not_change_judge_key() -> None:
    item = make_item()
    generated = make_generated("2路市电")
    key1 = build_judge_cache_key(
        item=item,
        generated=generated,
        heldout_answer="2路市电",
        judge_prompt_version="gongkan_eval_v1",
        judge_model="judge-model",
    )
    overlay = {"suggested_status": "partial_clue", "writeback_allowed": False}
    key2 = build_judge_cache_key(
        item={**item, "agent_overlay": overlay},
        generated=generated,
        heldout_answer="2路市电",
        judge_prompt_version="gongkan_eval_v1",
        judge_model="judge-model",
    )

    assert key1 == key2


def test_cache_file_append_read_uses_latest_record(tmp_path: Path) -> None:
    path = tmp_path / "judge_cache.jsonl"
    append_judge_cache_record(path, {"cache_key": "k1", "judge": {"label": "partial", "score": 0.5}})
    append_judge_cache_record(path, {"cache_key": "k1", "judge": {"label": "exact", "score": 1}})

    cache = load_judge_cache(path)

    assert cache["k1"] == {"label": "exact", "score": 1}


def make_item() -> dict:
    return {"form_item_id": "item_4", "row_index": 4, "question_text": "市电进线情况"}


def make_generated(answer_value: str) -> dict:
    return {
        "answer_value": answer_value,
        "answer_status": "answered",
        "source_chunk_ids": ["chunk_main"],
        "reference_source_documents": [],
    }
