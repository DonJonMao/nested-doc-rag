# Phase 5 — Evaluation and evidence workbench

This phase adds fixed challenge data, an offline evaluator, reproducible historical-method execution, an isolated Docker acceptance driver, and the evidence workbench fields required by the specification. **Real-model acceptance remains pending.** The configured embedding, rerank and chat endpoints disconnected during actual probes. Explicit protocol substitutes are labeled `models_kind=stub`; their answers and retrieval metrics do not establish business accuracy or improvement.

The exact base is `030300065e0d5041f581f573e772f7756e54abd6`. Phase 4B is committed as `08ea6eb9deb7c4ebd4c6307e2b2956378de3fc9b`. Phase 5 execution uses the working tree on `feat/evidence-grounded-vnext`, with file/config/image hashes retained in its ledgers. Results and unresolved gates are recorded in [PHASE5_RESULTS.json](PHASE5_RESULTS.json).

## Fixed inputs and evaluation

[The dataset declaration](datasets/manifest.json) binds three independent knowledge/form pairs and [30 fixed challenges](datasets/gold.jsonl). All three use new native Excel/Word knowledge, a new template and ten actual parsed fields. Source filenames, physical addresses, native text and hashes are independently declared before prediction. All 58 required/decoy locators were checked against native ingestion output. Independent regeneration reproduced the declared file hashes and gold bytes.

The seven primary categories contain 5/5/4/4/4/4/4 cases: same value/different field, different room, current/planned, numeric substring, target/global conflict, table detail and second-round evidence. There are 29 answerable cases and 28 preserve-eligible writes. C28 protects an existing human value; C30 must abstain because a required fact is absent. A separate F3 formula control does not inflate the 30-field denominator.

[PHASE5_EVALUATION.md](PHASE5_EVALUATION.md) defines the metrics, denominators, unknown populations and failure semantics. The evaluator scores actual retrieved packs against the independent source locators, checks the actual workbook and requires a raw primary-only counterfactual for answer gain. A selected ID or an artifact-validation pass does not establish factual correctness. Missing provider usage is unknown, never zero. Offline scorer requests are zero; that does not mean the preceding run made zero model calls.

`vnext_ablation.py` exports actual source revisions A0 `0303000`, A1 `300832c`, A2 `e41ef99`, A3 `ac91742`. It runs their ingestion, actual runner, writer and validator. A common current form parser supplies fields directly to each runner to isolate the retrieval method from the historical filename/row-selection bug; this method experiment does not prove A0's fresh-upload product flow. A0–A3 retain their historical pre-preserve writers. Current preserve behavior is checked separately in Docker.

Experiments reject nonempty output identities, verify and record copied input bytes/hashes, and remove host configuration overrides before loading the frozen method config. The retrieval observer passes the original arguments and result through unchanged; native authority takes precedence in scoring. The corrected A3-primary uses an explicit experimental adapter with the identical full A3 configuration, complete first-round retrieval and budgets, first sufficiency check, hit annotations, answer prompt and gate; only supplementary acquisition is disabled. Historical source files remain unchanged. It is recorded separately from A2.

The retained `artifacts/vnext/phase5/ablation-stub-03/` contains twelve completed method/pair executions, successful stage validators and twelve generated offline reports. Its old primary control also dropped `target_table_detail` and bypassed sufficiency; its Answer Gain compares coupled interventions and cannot isolate supplementary retrieval. Original reports and raw files remain intact. Earlier attempts `ablation-stub-01` and `-02` also remain available; their shared request ledger cannot identify per-run request counts.

The corrected explicit-stub experiment is `artifacts/vnext/phase5/ablation-stub-04/`: twelve method/pair runs, three strict primary controls, twelve offline evaluations and all main/primary validators passed. Main and primary configuration files match byte-for-byte; all 30 primary fields use one acquisition round and match the main run's first query and measured Qdrant calls. The evaluator now excludes unverified annotations from quality denominators. These remain protocol diagnostics, **not true-model accuracy or evidence of an improvement**. See [evaluation corrections](EVALUATION_CORRECTIONS.md).

## Historical compatibility

`vnext_prepare_old141.py` prepares exactly the old workbook's 141 target fields outside the original workspace. The closed-book input uses whitelisted field text and empty examples; existing/heldout answers never enter the prompt. This excludes 69 historical examples equal to the heldout human value. A separate original-input replay file preserves historical semantics.

The blank workbook clears only the 141 ordinary target values in native OOXML; formulas and untouched ZIP members retain their bytes. The heldout comparator is explicitly `legacy_heldout_unverified`, not independent factual gold. Old37 replay observes 141 fields, 37 historical writes and 104 review fields, and passes the current validator's legacy read path. Its quality metrics are null; it is not a current live run.

Live old141 compatibility still requires the configured 4096-dimensional embedding service, a copied legacy index and separate blank/preserve runs. Independent native evidence/status review is required before old141 quality claims; it is not an additional all-141 human-signoff gate. The original legacy index and templates must never be opened by a writing client. The 64-dimensional fresh-data protocol substitute cannot query those old vectors.

`vnext_old141.py` prepares separate blank/preserve runs and copies each index before opening a client. Final-source dry preparation in `old141-live-prepared-02` found 9,533 points and 4,096 dimensions in both copies, verified file sizes/hashes, and left original inputs unchanged; no model call or fill was executed. The earlier prepare01 is retained as older dry evidence. Bare historical target cells are sheet-qualified in the copied closed-book inputs to avoid active-sheet dependence. Private model env is read into an isolated mapping and only the configured credential passes into execution; it cannot override the frozen method config. Stub execution requires an explicit note. The `real` label on a prepared command is its intended execution mode, not evidence of a completed real-model run.

## Evidence workbench

Python → Go → UI now carries native XLSX cell ranges, Word table/row/paragraph indexes, acquisition rounds, missing facts, evidence gain, actual query counts, final sufficiency, and finalized old/new values and writeback policy. Address metadata comes from retrieval authority; final values come from the actual audit. Native Word indexes are already one-based and are displayed literally. Zero indexes/counts remain zero; absent measurements remain unknown.

The UI shows the value after writeback, including a preserved human value, rather than mislabeling it as the model proposal. Acquisition information is optional, so older artifacts still display. No permission or writeback gate is changed by these fields. Component/unit tests cover native addresses, legacy fields, null/zero and escaped HTML; no browser screenshot or visual acceptance is claimed.

Docker attempt 002 exposed a reference-classification defect: adding `evidence_kind` to review display metadata made the validator treat partial clues as typed references. The fix marks eligible review display references `review-display-v1`. Nonconfirmed display clues retain diagnostic quotes without gaining source text, offsets or confirmed write authority. Any typed-only field still forces strict validation, and confirmed references always require strict authority. Native partial/empty/forged-quote and disguise regressions cover this boundary. Replaying the original 002 raw/authority bytes through the current writer passes strict validation; that offline replay is not a fresh Docker pass.

## Fresh Docker driver

`vnext_docker_e2e.py` separates `prepare`, `verify-clean`, external Compose startup and `run`. It never builds, starts or removes Docker itself and never silently substitutes a model. The acceptance Compose has six services, eight new project volumes and no knowledge seed or historical input mount. Each pair creates a new workspace and target/global KBs in the same collection.

The driver uploads the real sources, waits for ingestion, verifies scoped counts/smoke receipts and activated versions, uploads the real template, creates a pinned fill, checks SSE and Last-Event-ID replay, downloads the registered artifacts and convenience workbook, runs both worker and downloaded validators, and checks actual input-cell/formula preservation. Fill completion is read from `raw_status`; immutable scopes and output path come from POST creation, while progress comes from GET detail. API-normalized `status=completed` is not a domain job status.

Retained attempts:

| Identity | Observation |
| --- | --- |
| `stub-20261003-001` | Driver changed before startup; invalidated without running |
| `stub-20261003-002` | Native upload/ingestion/publication passed; F1 fill rejected by the display-reference defect |
| `stub-20261003-003` | F1 fill actually succeeded; driver interrupted because it waited on public `completed` instead of domain `succeeded`; full acceptance incomplete |
| `stub-20261003-004` | All three pairs passed the corrected fresh engineering acceptance; explicit stub models |

Failed/interrupted logs, snapshots, API responses and volumes are retained. Only the explicitly named old acceptance stacks were stopped; original data was preserved. Dummy credential overlap caused some stub model names to be redacted in the old ledgers; the final run has a separate public configuration/model-script receipt. Upstream weight identity remains unknown for real services.

## Reproducing true acceptance

Run from the vNext checkout. Prepare a private model env file containing the configured credential variable; do not put its value in command arguments or logs. First probe the actual services:

```sh
PYTHONPATH=src python scripts/vnext_model_preflight.py \
  --config config/docker.yaml --models-env /path/to/private-models.env \
  --out artifacts/vnext/phase5/model-preflight-new.json
```

Only after the services pass, build both images and use a previously unused project/state identity:

```sh
docker build -f go-server/deployments/Dockerfile.api \
  -t datacenter-vnext-api:vnext go-server
docker build -f go-server/deployments/Dockerfile.worker \
  -t datacenter-vnext-worker:vnext .
python scripts/vnext_generate_datasets.py --check \
  --output-root artifacts/vnext/phase5/datasets-new-check
python scripts/vnext_docker_e2e.py prepare \
  --models-kind real --models-env /path/to/private-models.env \
  --model-config config/docker.yaml \
  --project vnext-real-acceptance-001 --api-port 18083 \
  --state-dir artifacts/vnext/phase5/docker/real-acceptance-001 \
  --api-image datacenter-vnext-api:vnext \
  --worker-image datacenter-vnext-worker:vnext
python scripts/vnext_docker_e2e.py verify-clean \
  --state-dir artifacts/vnext/phase5/docker/real-acceptance-001
docker compose \
  --env-file artifacts/vnext/phase5/docker/real-acceptance-001/runtime.env \
  -p vnext-real-acceptance-001 \
  -f go-server/deployments/docker-compose.vnext-acceptance.yaml up -d --no-build
PYTHONPATH=src python scripts/vnext_docker_e2e.py run --evaluate \
  --state-dir artifacts/vnext/phase5/docker/real-acceptance-001
```

Any used identity must be replaced with a new one. All three true fresh pairs must pass before the larger true method comparison and old141 runs:

```sh
PYTHONPATH=src python scripts/vnext_ablation.py \
  --dataset-dir artifacts/vnext/phase5/datasets \
  --manifest docs/vnext/datasets/manifest.json \
  --models-config config/docker.yaml --models-env /path/to/private-models.env \
  --models-kind real --out-dir artifacts/vnext/phase5/ablation-real-new \
  --primary-counterfactual --execute
```

Score every method/pair outside its immutable run directory with `vnext_evaluate.py`, `--models-kind real`, the recorded namespace map, and the recorded observer/primary inputs where required. A scorer exit zero means the report was generated, not that a quality threshold passed.

The historical live runner requires an explicit embedding dimension declaration and verifies the copied collection. This declaration does not prove the provider's actual returned dimension. Prepare a new identity and add `--execute` only for a real live run:

```sh
PYTHONPATH=src python scripts/vnext_old141.py \
  --models-config config/docker.yaml --models-env /path/to/private-models.env \
  --models-kind real --embedding-dimension 4096 \
  --out-dir artifacts/vnext/phase5/old141-real-new --execute
```

## Verification and unresolved gates

Final frozen checks passed: 878 Python tests (zero failures/errors/skips, 75 compatibility warnings), 520 Go leaf cases with race checking (zero failures/skips), 39 frontend cases, frontend typecheck/build, the three required real PostgreSQL suites, Ruff, gofmt, Go vet and diff checks. The earlier 845-test Python run and the six Docker/27 old141 helper regressions remain separately recorded. All 103 frozen original-workspace files are unchanged. These checks are separate from Docker/model results. Defaults remain sufficiency on, schema-first off, AgentScope/multi-agent execution off, and writeback preserve; manifest remains 1.3.

The corrected Docker run processed all 30 fields, passed both validators for every downloaded pair, and observed zero changes to 109 originally nonempty cells, including the F3 formula. The F3 human target value remained intact. Four changed Python production modules in the running Worker were SHA256-checked against the tested working tree with no mismatch. All three offline reports retain `models_kind=stub` and an explicit no-real-model-quality claim. A second real-service preflight again failed all three endpoints with `RemoteDisconnected` after about five seconds each. The owned acceptance stacks were stopped after evidence capture; their volumes/downloads remain, and the original deployment was not changed.

After the migration and evaluation audits, the current full Python suite passes 909 tests with no failures/errors/skips and 75 compatibility warnings. Go/frontend production code has not changed since the independently recorded 523 Go race leaf passes and 39 frontend passes. The original Phase 5 counts above remain historical execution facts. Latest evidence and service probes are in [EVALUATION_CORRECTIONS_RESULTS.json](EVALUATION_CORRECTIONS_RESULTS.json).

No real-model improvement, real clean-Docker final acceptance, old141 live execution or business generalization is claimed. Real endpoints, true A0–A3/challenge runs and true fresh Docker pairs remain required. Old141 live blank/preserve compatibility and baseline reproduction are pending; independent native evidence/status verification is required before any old141 quality claim. The native review currently contains 57 partial agent reviews and 84 candidate-only records, with zero verified gold exports. Existing deployment limits from [Phase 4B](PHASE4B.md) remain: stop old binaries before migration, no mixed-version rolling deployment, one shared target/global collection, no GC and no event outbox. The implementation goal is not complete until the true-model gates are resolved.
