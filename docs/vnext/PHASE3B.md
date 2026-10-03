# Phase 3B — Optional schema-first Excel retrieval

This phase adds the independent A4 retrieval variant: choose the field schema first, then retrieve Excel values constrained to its field family. It reuses the existing Qdrant collection, embedding endpoint and reranker. `retrieval.schema_first_enabled=false` remains the default in the dataclass and all three shipped profiles, so Phase 3's default sufficiency-guided strategy remains the A3 path. CLI `--schema-first-enabled` opts into A4; `--no-schema-first-enabled` opts out.

## Structural schema and native ingestion

Eligible native Excel records receive `retrieval_object=evidence` and a structural `field_family_id`. A family is derived from namespace, knowledge-base identity, relative path, sheet, category path, field label, ordered column headers and explicit unit. Changing the answer/value does not change that family or the schema text. Labels, headers and unit descriptors come from recognized native structure; unit extraction does not infer units from answer values. Unknown free-text rows do not become field schemas by guesswork.

`build_ingestion_records` continues to return physical evidence records only. `run_knowledge_ingestion` separately constructs schema records and indexes both objects in the same collection. A schema payload carries `schema_id`, its own point identity, family and scope, field/sheet/category/header/unit descriptors, and value-free `schema_text`/`text_for_embedding`. It has `retrieval_object=field_schema` and `corpus_layer=field_schema`, with no evidence ID/kind, answer value or original quotation text. A field with a clearly labeled empty/formula/image value can have a schema while its physical row remains `table_row` with no invented field value.

`ingestion_manifest.jsonl` and existing `record_count`/`upserted_count` continue to count evidence. `field_schema_manifest.jsonl` archives auxiliary schemas. `schema_record_count`, `schema_upserted_count`, `total_point_count` and `total_upserted_count` make the additional index objects explicit; document evidence/chunk counts are not inflated by schemas. Existing native addresses, original text, hashes and attachments remain evidence properties.

## Selection, constraints and failure behavior

For each acquisition, the runner forms one schema query per required, evidence-bearing slot; if there is no such slot it uses the field query. A targeted supplement uses its concrete missing facts as schema queries. Each namespace's schema selection is cached across eligible Excel/table layers within that acquisition. Each distinct query is reranked to at most one valid schema, and the selected families are unioned.

The value search retains the configured namespace, corpus, evidence-kind/source and layer constraints and adds family filtering for Excel evidence. Word/TXT evidence stays eligible under its existing constraints; this phase does not convert it into an Excel schema. The Phase 3 target-before-global ordering, scoped evidence merge, at-most-two acquisitions and independent writer/artifact authority checks remain in force.

Selection metadata explicitly distinguishes two failure conditions:

- `fallback=no_schema` and `field_family_ids=null`: all schema queries in that namespace found no schema candidates; use the existing value search for compatibility with a schema-less index.
- Schema candidates exist but no valid family is selected: `fallback=null`, `field_family_ids=[]`, with `invalid_schema_selection` diagnostics. Excel retrieval stays constrained and cannot fall back to unrelated high-scoring values. A selected family with no actual value also remains constrained. If one slot has no schema and another has a valid family, the selected family still restricts the namespace; the missing slot does not broaden it.

Ordinary evidence retrieval excludes auxiliary schema points even if a caller includes the auxiliary corpus layer. The resolver rejects a point marked `field_schema` as source evidence, including one given forged evidence-shaped fields. Schema IDs cannot enter answer citations as locatable native evidence. Family selection is a retrieval constraint, not a declaration of factual sufficiency: Phase 3 checks the resulting physical evidence and abstains when facts remain missing.

## Measurements, trace and resume

Per-round metadata contains `schema_first.enabled` and `schema_first.selections`, including namespace, query/candidate/selected schema IDs, family IDs and fallback/diagnostics. Runner traces `field_schema_selected` with the acquisition round. The existing actual Qdrant counter includes both schema and value `query_points()` attempts; no new planned-layer estimate is substituted for observed calls.

In the tested unretried, single-slot default plan, primary retrieval makes one schema query and two value queries: **one acquisition, three actual Qdrant calls**. A full supplement makes one target schema query, one global schema query and five value queries: **two acquisitions, ten calls in total**. Multiple slots, skipped layers, configured subsets and retries change actual call counts without relaxing the maximum of two sufficiency acquisitions.

The acquisition fingerprint adds `schema_first_enabled` and `field_schema_contract_version` (`field-schema-v1` when enabled). Changing the flag or contract version rejects resume before models, checkpoint parsing or prior artifact replacement. A value-only injected retrieval function cannot run the A4 path without a schema/value backend.

## Verification and remaining acceptance

Six new integration cases use real native XLSX ingestion, disk Qdrant, actual CLI execution and the production artifact validator. The fixture deliberately assigns an air-conditioning capacity value a higher cosine score than the equal UPS capacity value, while the UPS schema ranks first. This control proves that the field-family filter, rather than a favorable value-vector ranking, excludes the wrong field. Separate native target/global/other-room indexes and current/planned fields exercise scoped selection. The tests also cover auxiliary-point isolation, explicit no-schema fallback, a selected empty-value family through two acquisitions, no final answer/confirmed write on a remaining gap, and flag/contract resume rejection with unchanged earlier artifacts.

Embedding, rerank, sufficiency and answer services are explicit deterministic localhost substitutes. Vectors are deliberately controlled in the real disk index, and model responses serve known fixture facts. This is an execution/constraint test, not a real-model semantic or accuracy result. A4 quality metrics, fresh/challenge evaluation, A0–A4 model comparisons and clean Docker E2E acceptance remain unverified. Phase 4/4B write-policy/version activation work is outside this phase.

- Python full suite: **624 passed**, zero failures/errors/skips, **86.71 seconds**. Four expected warnings exercise explicit legacy evidence fallback.
- JUnit records **24** field-schema module cases, **17** schema Qdrant cases, **5** schema layered-retrieval cases and the **6** new integration cases. Related regression runs are separate checks and are not counted as newly added module tests.
- Go `go test ./...`, Ruff across `src`/`tests`, and diff whitespace checks: passed. Existing Phase 3 integration and retrieval regressions remain green in the full suite.
- All **103** frozen original-workspace files have unchanged hashes. External model calls and clean Docker E2E runs: **zero**.

Evidence is in `artifacts/vnext/phase3b/`: `python.log`, `python.xml`, `go.log`, `ruff.log`, targeted JUnit files, `commands.json` and `original-workspace-integrity.json`. Machine-readable results are in [PHASE3B_RESULTS.json](PHASE3B_RESULTS.json); A4's engineering-contract status and remaining semantic acceptance are recorded in [ACCEPTANCE.md](ACCEPTANCE.md).
