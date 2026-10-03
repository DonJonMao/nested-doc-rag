from __future__ import annotations

from nested_doc_rag.agent.mas.schemas import EvidenceStateKind, parse_evidence_diagnosis


def test_parse_evidence_diagnosis_falls_back_when_llm_omits_field() -> None:
    diagnosis = parse_evidence_diagnosis(
        {
            "answer_status": "partial_clue",
            "answer_value": "未找到",
            "confidence": 0.44,
            "missing_fields": ["台数"],
        },
        [{"chunk_id": "chunk_initial"}],
    )

    assert diagnosis.state_kind == EvidenceStateKind.MISSING_INFO
    assert diagnosis.missing_information_need == "台数"


def test_parse_evidence_diagnosis_tolerates_unknown_values() -> None:
    diagnosis = parse_evidence_diagnosis(
        {
            "answer_status": "answered",
            "answer_value": "2路市电",
            "confidence": 0.9,
            "source_chunk_ids": ["chunk_main"],
            "evidence_diagnosis": {
                "state_kind": "unknown_state",
                "failure_modes": ["bad_mode"],
                "sufficiency": "",
                "candidate_answers": [{"value": "2路市电", "supporting_chunk_ids": ["chunk_main"]}],
            },
        },
        [{"chunk_id": "chunk_main"}],
    )

    assert diagnosis.state_kind == EvidenceStateKind.SUFFICIENT
    assert diagnosis.candidate_answers[0].supporting_chunk_ids == ["chunk_main"]
