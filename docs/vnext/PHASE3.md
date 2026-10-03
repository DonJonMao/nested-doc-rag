# Phase 3 — Sufficiency-guided targeted retrieval

The production Step15 path now acquires target structured facts and table details first, checks whether the necessary field facts are covered, and retrieves once more only for a concrete gap. A field has one primary acquisition (`retrieval_round=0`) and at most one targeted supplement (`retrieval_round=1`). There is no third acquisition in this strategy. The existing retriever, Qdrant collection and reranker are reused.

## Execution and defaults

The primary candidates are `target_structured_fact` and `target_table_detail`. Each candidate list is intersected with the configured layered plan, so a valid structured-only or text-only plan remains valid. An empty primary plan makes no backend attempt. An externally requested unconfigured layer is still rejected; this intersection does not broaden retrieval scope.

The sufficiency request uses the current question, instructions, required slot schema, room context and current evidence pack. It is separate from answer arbitration and returns exactly four fields:

```json
{
  "sufficient": false,
  "missing_facts": ["UPS容量"],
  "supporting_evidence_ids": ["an-id-in-the-current-pack"],
  "reason": "Current evidence supports the UPS brand but does not give capacity"
}
```

This is a semantic decision object, not a confidence scalar or a manually weighted score. `sufficient=true` requires no missing facts and at least one valid supporting reference. The system separately adds normalization diagnostics; these are not extra model output fields. Form and slot inputs are whitelisted, heldout/gold/previous answers are excluded, format examples are marked as examples, and retrieval score/rank fields are removed from the sufficiency request. Instructions explicitly treat retrieved text as data and prevent general/global background from substituting for the target room's actual parameters.

A sufficient primary pack proceeds directly to final answer arbitration. Otherwise the system builds a query containing the target room, original field and specific `missing_facts`. The supplement may revisit the two primary layers and open `target_text_detail`, `global_detail` and `global_intro`, within the existing configured plan and namespace/corpus constraints. Evidence is merged by evidence ID, ordered with target before global and structured facts before raw text. Repeated IDs retain their first acquisition metadata; new evidence keeps `retrieval_round=1` and its `triggered_by` facts in the final prompt and archived authority.

The merged pack is checked again. If it remains insufficient, the system produces an explicit `system_sufficiency_abstention` with `partial_clue` when hits exist or `not_found` when the pack is empty, records the missing facts, skips the final answer model call and forbids confirmed writeback. The original raw/overlay separation remains: an actual model answer is not silently rewritten to fabricate this abstention.

`RetrievalConfig` and all three shipped YAML profiles (`default.yaml`, `docker.yaml`, `local.example.yaml`) now default to `retrieval.sufficiency_enabled=true`, `agentscope.enabled=false`, `agentscope.mode=off` and `agentic_mas.enabled=false`. CLI `--sufficiency-enabled` and `--no-sufficiency-enabled` explicitly select this strategy or the legacy layered path. YAML, environment and CLI overrides retain their tested precedence.

Legacy MAS remains available through an explicit opt-in. `--agentic-mas` selects the legacy agentic configuration and disables sufficiency when no sufficiency flag was supplied. A configured non-off MAS mode requires sufficiency to be disabled; combining it with explicit `--sufficiency-enabled` is rejected before run execution. This prevents legacy replanning from stacking additional acquisitions onto the new two-acquisition strategy. Legacy tests and the offline `generate_evidence_contract_fixture.py` explicitly keep sufficiency disabled; the fixture script was not executed in this phase.

## Authority, failure handling and resume

Supporting IDs must belong to the current check's pack and pass the existing `resolve_evidence_refs` checks for addressable source text, native/explicitly verified text space, declared hashes and source addresses. Unknown or unresolvable IDs, missing/extra schema fields, wrong types, contradictory sufficiency and missing support fail closed. Exhausted invalid-JSON retries also become an insufficient decision with diagnostics and a bounded supplement, rather than an answer. Other exhausted service failures follow the existing field-failure path and do not authorize writing.

The pack supplied by the scoped retrieval path remains the source authority. Different source origins for the same ID across rounds produce an observable conflict and veto final arbitration even if the model says sufficient. Phase 2's per-field `retrieval_evidence.jsonl`/checkpoint authority, independent writer checks, typed references and artifact validation remain in force. Model-supplied file/sheet/cell coordinates cannot replace native source metadata. Addressability and hash validation do not prove semantic entailment; auxiliary embedding/parent text also does not become independent verified gold.

`form_input_snapshot.json` now freezes an `acquisition_contract`: strategy version, sufficiency flag, MAS mode, answer prompt version, sufficiency prompt version (`evidence-sufficiency-v1`), applicable agentic prompt version, configured layered-plan hash, allowed corpus layers and collection. This extends the existing template/parser/form-items/selection/evidence-contract/namespace/room-context fingerprint. CLI and Runner validate it before loading checkpoints, contacting models or replacing archived inputs. A strategy or prompt change requires a new run directory; historical snapshots cannot silently become a new-strategy resume.

## Measurements and trace

`acquisition_rounds` measures primary/supplement acquisitions. `qdrant_query_calls` independently measures actual `client.query_points()` attempts: the counter increments immediately before the backend call, including failed attempts. Per-acquisition deltas are aggregated across retrieval retries. Unknown/uninstrumented backends report `null`, not a count inferred from the number of planned layers. Empty or rejected plans report zero backend calls and zero retrieval attempts.

The default five-layer plan with successful, unretried retrieval has two actual Qdrant queries for a sufficient primary pack, or seven queries for a primary plus full supplement. These are measured test scenarios, not fixed limits on backend calls: skipped layers, custom subsets and retries affect actual counts without creating a third acquisition. `retrieval_attempts`, retrieval/sufficiency latencies, per-round queries/hit counts, evidence gain, final sufficiency and source conflicts are archived in prediction `validation.acquisition`.

Trace events include `primary_retrieval_completed`, `evidence_sufficiency_checked`, `targeted_retrieval_started`, `targeted_retrieval_completed` and `answer_arbitrated`. The final event records whether its origin is the model or system abstention. Each second round exposes its missing facts and targeted query.

## Verification and remaining acceptance

- Python full suite: **572 passed**, zero failures/errors/skips, **69.78 seconds**. Four expected warnings exercise explicit legacy evidence fallback.
- Go `go test ./...`, Ruff across `src`/`tests`, and diff whitespace checks: passed.
- Ten new integration cases invoke actual `ingest-knowledge`, `run-step15-agent` and `validate-artifacts` CLI processes with native XLSX/TXT/DOCX extraction and disk Qdrant. The form parser, runner, writer, Evidence worksheet, checkpoint handling and validator use production code.
- The cases verify one acquisition/two actual queries for sufficient primary evidence; two acquisitions/seven queries when native TXT/DOCX capacity supplements an XLSX UPS brand; ID deduplication, preserved round metadata and actual sheet/row/cell/paragraph locations. Still-missing facts, malformed sufficiency objects and unknown support IDs skip final answer generation and confirmed writing. Strategy, answer-prompt and sufficiency-prompt changes reject resume without model calls or byte changes to earlier run artifacts.
- The prior 29 runtime form/addressed evidence integration cases also pass. Runner tests additionally cover retry accounting, invalid JSON, conflicting origins, custom plan subsets, empty plans and MAS incompatibility. Real disk-Qdrant metric tests cover skipped layers, zero/failed calls and unknown counters.
- All **103** frozen original-workspace files have unchanged hashes.

Only the embedding, rerank and chat HTTP endpoints are explicit deterministic localhost substitutes. Sufficiency and answer replies are routed independently by their system prompts. External model calls: **zero**. Clean Docker E2E runs: **zero**. These results prove execution and failure contracts; real-model sufficiency/answer quality, A0–A3 effect comparisons and final fresh/challenge/Docker acceptance remain unverified. Phase 3B/4/4B/5 are not completed by this report.

Evidence is in `artifacts/vnext/phase3/`: `python.log`, `python.xml`, `go.log`, `ruff.log`, `runner.xml` and `original-workspace-integrity.json`. Machine-readable results are in [PHASE3_RESULTS.json](PHASE3_RESULTS.json); requirement status is in [ACCEPTANCE.md](ACCEPTANCE.md).
