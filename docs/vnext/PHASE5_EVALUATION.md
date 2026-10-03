# Phase 5 evaluation contract

This document describes the fixed-data and offline scoring implementation. It does not claim that the real model service or clean Docker E2E has passed. The source requirements are [ACCEPTANCE §§3–6](ACCEPTANCE.md) and [implementation specification §§11/17](NESTED_DOC_RAG_VNEXT_IMPLEMENTATION.md).

## Fixed inputs and execution boundaries

- `docs/vnext/datasets/manifest.json` declares three independent anonymous native knowledge/form pairs, source/template hashes and formula controls. `gold.jsonl` contains 30 fixed challenges: 5 same-value/wrong-field, 5 same-field/wrong-room, 4 current/planned, 4 numeric-substring, 4 target/global, 4 table-detail and 4 second-round cases. `scripts/vnext_generate_datasets.py` owns their reproducible generation. Binary source files live under ignored `artifacts/vnext/phase5/datasets/`.
- Each pair has ten actual parsed target addresses. Gold facts and physical source locators are declared from native files, independently of predictions. The final scorer was checked against all 58 required/decoy native locators using the real ingestion parser without embeddings or model requests.
- The original repository remains read-only. Old141 is the exact subset `file_name=基地云机房信息调研表.xlsx`, rows 4–144, from the 563 historical records. The old workbook has 141 nonempty targets. Old37 is `artifacts/runs/reconstructed_relaxed25_retry5_20260630/`, with 141 predictions and 37 historical confirmed writes; similarly named runs with 34 or 39 writes are not substitutes.
- The existing mini `run-baselines` experiment is a lexical/deterministic demonstration. It uses stored answer values and prescribed method latency/cost; it cannot supply real A0–A3 results. `evaluation/field_metrics.py` remains a compatibility evaluator: its evidence-support check uses selected-ID presence and its recall uses selected source IDs. Phase 5 scores the retrieved pack against independent locators instead.

## A0–A3 and the primary counterfactual

Root's separate `scripts/vnext_ablation.py` owns execution using isolated exact source revisions:

| Method | Revision | Increment |
| --- | --- | --- |
| A0 | `030300065e0d5041f581f573e772f7756e54abd6` | Precise current writeback-37 base |
| A1 | `300832c` | Unified uploaded evidence |
| A2 | `e41ef99` | Addressable evidence and verified confirmed writes |
| A3 | `ac91742` | Sufficiency-guided primary plus at most one supplement |

The common adapter passes the fixed runtime-parsed fields directly to each revision's actual `Step15AgentRunner`. This isolates retrieval methods from old CLI filename/row selection; it is explicitly a method comparison, not proof that A0's fresh-upload product flow works. A0–A2 are not simulated by turning off one A3 flag. Schema-first A4 remains separate and disabled for the required comparison.

Embedding/chat/reranker identities, effective endpoints and nonsecret configuration hashes, corpus/form/gold hashes, candidate budgets, prompt differences and `judge=false` must be recorded. All four historical revisions precede Phase 4's preserve policy and use their actual historical writers on fixture copies. Nonempty/formula controls expose that safety difference; the final vNext preserve behavior is a separate current-runtime/Docker acceptance claim.

Answer gain requires a raw A3-primary counterfactual: the same A3 revision, model, prompt and primary candidate budget, with supplement retrieval disabled. A2's full layered retrieval is not a primary-only answer baseline. Counterfactual extra calls must be logged separately. If a real service is unavailable, preserve that failure and mark live acceptance pending; do not silently use a stub or historical response as a live result.

Keep every first-round layer and its order/budget, the first-round sufficiency decision, acquisition annotations and generation inputs unchanged. Disabling sufficiency or dropping first-round evidence invalidates attribution to supplementary retrieval alone. The corrected adapter uses `A3-primary-only-no-supplement-v1`; main and primary configuration bytes are identical. The older `ablation-stub-03` control changed both the candidate pool and sufficiency, so its Answer Gain cannot establish that attribution.

## Scorer interface and gold semantics

```bash
python scripts/vnext_evaluate.py \
  --gold docs/vnext/datasets/gold.jsonl \
  --dataset-id f1 \
  --dataset-manifest docs/vnext/datasets/manifest.json \
  --run-dir artifacts/vnext/phase5/ablation/A3/f1/run \
  --primary-predictions artifacts/vnext/phase5/ablation/A3-primary/f1/run/predictions_raw.jsonl \
  --out-dir artifacts/vnext/phase5/reports/A3/f1 --method A3 --k 5
```

These are command forms; existence of the scorer does not mean the above model execution has happened. For API-generated namespaces add `--namespace-map PATH`, a JSON object mapping fixed dataset labels such as `f1` and `global` to the actual frozen runtime namespaces. The driver may alternatively create a pair-local ten-record gold subset. A shared multi-pair gold file requires `--dataset-id` to avoid ambiguous targets across pairs.

Input precedence is `predictions_raw.jsonl` over the compatibility alias. New runs provide `retrieval_evidence.jsonl`; old addressed runs can use `eval_results.jsonl.top_hits`. A0/A1 execution may use `--retrieval-authority PATH` to name an actual observed `run_step15_retrieval` return-value sidecar, with file hash and explicit provenance. The observer preserves the original call parameters, calls and returned hits. Native authority always takes precedence when present; neither the evaluator nor observer fabricates missing hits. Selected chunk IDs identify the model's choices in that raw pack but never establish gold support. The scorer matches file identity, declared namespace, physical address and native source text/hash. It accepts actual Word index zero, rejects missing locators, and supports native XLSX cells, DOCX table rows and paragraphs. Document hashes can additionally bind locators through the ingestion payload's `source_document_hash`/`document_hash` aliases. Random document/version/point UUIDs are not required to appear in gold.

Gold joins by actual sheet/cell, with explicit field identity as a fallback. Historical single-sheet bare targets can join by row plus the same bare cell; duplicate candidates fail instead of selecting an arbitrary last row. `required_evidence` declares exact supporting facts. Multiple locators with the same `fact_id` are valid alternatives for one fact. Optional `relevant_evidence` defines a broader relevant pack; when absent, relevant and exact populations are equal by design. Decoys are independently labeled `wrong_field`, `wrong_scope`, `planning`, `global_conflict` or `substring`; unlabeled hits are counted as unknown for field-error scoring rather than guessed from a number match.

Raw answer statuses are `answered`, `partial_clue`, `not_found` and `conflict_unresolved`. Audit statuses such as `confirmed`/`flagged` are separate. A full-value equality or a declared accepted alias establishes answer agreement. Numeric normalization consumes the entire scalar and preserves unit dimensions: `0.5MVA` can match `500kVA`; `1500kVA`, `500kW` and a composite answer containing `500kVA` do not automatically match `500kVA`.

Outputs are `report.json`, per-case `cases.jsonl`, and `report.md`. The JSON publishes numerators, denominators, populations, unknown counts, missing-data reasons, input hashes, failed/missing fields and every failed invariant. Script exit zero means report generation succeeded; no quality threshold is inferred. Reports must be written outside the input run directory.

## Metric definitions

Gold-derived scores require an explicit verified declaration and assessable annotations. Missing verification is not implicitly true; heldout and candidate/partial-review labels remain unverified even when native locators exist. Unverified entries are unknown and excluded from quality denominators. The frozen `vnext-evaluation-dataset-v1` dataset's explicit `declared_synthetic_domain_rules` origin remains eligible without adding a human-signoff requirement. Reports expose declaration, answer-label and evidence-annotation eligibility separately. Actual acquisition/call telemetry and separately verified input protection remain observable.

| Required metric | Numerator | Denominator / population |
| --- | --- | --- |
| Evidence Recall@K | Sum of each case's fraction of relevant fact groups recovered in ranked retrieval top K | Cases with declared relevant evidence and available retrieval, including explicitly failed/missing cases as zero when no pack exists |
| Exact Field Evidence Recall@K | Sum of each case's fraction of exact field/scope/status/address/native-text fact groups recovered in top K | Cases with `required_evidence`; same-valued wrong-field/room/planning/global locators earn no exact credit |
| Wrong-Field Retrieval Rate | Independently labeled wrong-field hits in top K | Gold-classified top K hits in cases containing wrong-field decoys; unclassified hits are separately unknown |
| Target-vs-Global Confusion Rate | Cases where contradictory global evidence precedes every matching target support | Cases containing an independently declared target/global conflict and an available pack; missing target retrieval remains visible in recall |
| Answer Accuracy | Nonfailed predictions matching the gold status and complete value/accepted alias | All verified gold cases, including missing predictions and field failures |
| Abstention Precision | Actual nonfailed abstentions on gold nonanswerable cases | Actual `not_found`/`partial_clue`/`conflict_unresolved` predictions with known gold answerability; system/model failure is not credited as correct abstention |
| Unsupported Answer Rate | Answered fields lacking all required independently supported facts, choosing absent IDs or disagreeing with gold-supported value | Answered fields whose support can be assessed from independent gold and the raw pack; lack of support annotations is unknown |
| Writeback Precision | Written fields with correct raw answer, exact support, allowed policy and correct actual workbook value | Written fields with independently assessable gold, raw authority and actual downloaded/generated workbook |
| Writeback Coverage | Fields actually written | Independently declared writeback-eligible gold fields under the fixed policy; output files absent for claimed writes are unknown |
| Unsafe Overwrite Count | Unauthorized changes to independently known nonempty/formula targets | Count, with observed protected-target denominator and unknown count; compare gold original values/formulas with the actual `filled_form.xlsx`, not an audit's claim that old/new match |
| Second-Round Trigger Rate | Measured fields with exactly two acquisition rounds | Fields with raw acquisition measurements; more than two rounds is a failed invariant |
| Second-Round Evidence Gain | Number of newly recovered exact gold fact groups beyond the primary pack | Supplemented fields with per-hit round tags; this is gold-supported gain, separately from production's total unique-ID novelty |
| Answer Gain after Targeted Retrieval | Final correctness minus raw A3-primary correctness | Supplemented fields with verified assessable gold and a valid same-first-round A3-primary control; absent or nonisolating controls cannot support an attributable gain |
| Average Retrieval Calls/Field | Actual instrumented Qdrant query count including retries | Fields with raw measured calls; acquisition rounds and backend layer/schema queries are separate measurements |

No provider usage archive means token/cost usage is unknown, never zero. `model_usage.json`, when actually archived, is passed through as the observed provider usage. Unknown measurements and zero denominators yield JSON `null`, with reasons; measured subset ratios retain their explicit denominator and unknown population. Formula safety controls from the dataset manifest are scored without increasing the 30 challenge count.

## Old141 preparation and old37 replay

```bash
python scripts/vnext_prepare_old141.py \
  --original-root /Users/mao/projects/datacenter \
  --out-dir artifacts/vnext/phase5/old141-prepared

python scripts/vnext_evaluate.py \
  --run-dir /Users/mao/projects/datacenter/artifacts/runs/reconstructed_relaxed25_retry5_20260630 \
  --out-dir artifacts/vnext/phase5/old37-replay --replay-old37
```

The preparation emits a strict whitelisted `form_items_closed_book.jsonl` with empty `answer_example`; existing/current/heldout/status and suggested-query facts are removed. `heldout_answers.jsonl` is evaluation-only and explicitly unverified. An exact historical subset is separately named `form_items_legacy_replay.jsonl`. This distinction matters: 69 historical format-only examples exactly equal their heldout values and the old prompt includes those examples. Replay preserves historical input semantics; closed-book quality runs cannot carry those facts into the query/prompt.

The blank-target XLSX is a copy edited at native OOXML cell values. Formula cells remain untouched, all other ZIP members retain their bytes, and no style/layout redesign or formula recalculation is performed. Output inside the original repository or reuse of an existing output directory is rejected. The preparation report records both original and generated hashes and checks original bytes remain unchanged. Independent package preservation and cell/formula tests cover the edit.

Observed local results: old141 preparation produced 141 fields, cleared 141 ordinary target values and left original source hashes unchanged. Old37 readback observed 141 predictions/evaluations/audits, 37 confirmed writes and 104 skips; the current artifact validator accepts its manifest 1.1 through `legacy_read`. Replay reports `quality_metrics=null`, because historical judge scores and IDs do not supply independent gold or a current live rerun.

The old141 heldout values need independent evidence/status review before they become quality gold. A true original-template preserve test must retain human values; a lower write count under preserve is expected and cannot be repaired by silently overriding policy.

## Local verification and remaining evidence

The scorer/preparation tests verify real output-cell overwrite detection despite contradictory audit metadata, arbitrary runtime IDs, native text/scope/address rejection, zero-based Word locators, strict numeric comparisons, retrieval-rank recall, alternative evidence groups, per-field failures, raw primary answer gain, unknown telemetry, duplicate-target rejection, formula controls and source-package preservation. No model request is issued by either script or these tests.

Required final evidence remains separate: successful true-model A0–A3 execution, reviewed old141 live/replay distinction, clean Docker upload→ingest→fill→download for all three fresh pairs, final preserved workbook controls, complete run ledgers and archived provider/model identity. The existence of deterministic fixtures or a successful offline report cannot close those gates.
