# vNext baseline freeze

- Repository: DonJonMao/nested-doc-rag
- Exact base: `030300065e0d5041f581f573e772f7756e54abd6`
- Original branch: `ops/docker-worker-python-core`
- Implementation branch: `feat/evidence-grounded-vnext`
- Isolated worktree: `/Users/mao/projects/datacenter-vnext`
- Python: 3.13.13; Go: go1.26.4 darwin/arm64
- Production profile: `config/docker.yaml`, historical `writeback-37-final`
- Baseline test results: **199 passed**, excluding only the two newly added regression files; both new regressions fail with assertions as required. Go `go test ./...` passes.
- Historical workload: rows 4-144 (141 fields); asset availability and exact known writeback result will be audited independently.

## Ownership and migration

The original worktree contained existing staged/unstaged application changes and untracked artifacts. It remains unchanged. A binary diff, untracked copies, and SHA256 ownership manifest were saved locally under `/tmp/datacenter-vnext-origin-20261002`. Phase 0 is run on the exact clean base so both historical upload bugs can be reproduced before inheriting the compatible existing work.

## Interpretation of the specification

- Fresh-upload final acceptance uses at least three fixtures and a challenge set of at least thirty cases.
- All write policies protect formulas; `overwrite_all` is CLI-only and concerns ordinary existing values.
- Two retrieval rounds mean one primary acquisition and at most one targeted supplement, independent of the number of layer or schema/value Qdrant calls within a round.
- The final arbitration must abstain or return a partial clue if merged evidence still lacks required facts.
- Evidence addressing retains existing original-text provenance, hashes and Unicode interval semantics.
- Index versioning must enter physical point IDs as well as payload/filter so V2 cannot overwrite V1.

## Known baseline gaps

Uploaded source types are filtered out by the production layered plan. Uploaded form templates do not drive runtime fields. Confirmed answers can overwrite non-empty ordinary cells even in safe mode. Ingestion deletes a namespace before embedding/upsert. DB version records do not freeze a physical Qdrant snapshot.

## Phase 0 evidence

- A: native uploaded fact is present in real local Qdrant; the baseline Docker layered plan returns no vector or reranked hits.
- B: real CLI with a renamed fresh template and no historical artifact passes zero fields to its runner.
- External model calls for both regressions: zero.
- Full commands, logs and JUnit XML: `artifacts/vnext/phase0/`; tracked outcome: `PHASE0_RESULTS.json`.
- The prior workspace already fixes A; that fix will be inherited only after these baseline tests exist and their failure is recorded.
