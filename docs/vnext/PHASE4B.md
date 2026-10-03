# Phase 4B — Immutable knowledge builds and pinned fill runs

Knowledge updates now build and validate an isolated candidate UUID before changing the serving pointer. The implementation uses the existing Qdrant collection and PostgreSQL knowledge version UUID; the ordinal version number is only for display. No namespace-wide deletion, collection-per-upload scheme or new database is introduced.

## Build, validation and publication

`PGXBuildStore.CreateBuild` locks the KB and creates the candidate, ingestion, worker job, frozen source snapshot and source pins in one transaction. The snapshot records workspace/KB/version UUIDs, collection/namespace and every document/file UUID, object key, filename, SHA256, byte size and document role. Canonical JSON bytes and their SHA256 are persisted separately from the JSONB representation.

The worker materializes only the frozen object identities. Inputs live under a private version UUID root; each snapshot relative path is `documentUUID/fileUUID/filename`. Equal filenames cannot overwrite another document. The worker and Python verify raw snapshot bytes, source hashes/sizes, UUIDs, supported paths and source stability. The original document/file lists are never re-read to change a retry's inputs.

Evidence and auxiliary schema point UUIDs include the immutable version UUID. Candidate retries delete only their exact namespace/KB/version scope. V1 remains physically present when V2 embedding, upsert or validation fails. Legacy ingestion is explicitly observable and never deletes a namespace.

Python produces `kb-index-validation-v1` only after real scoped evidence/schema counts, source verification and a native smoke query pass. The receipt, original `input_snapshot.json` and index artifacts are archived. The exact shared protocol is in `go-server/internal/knowledge/protocol.go`; Go validates its scope/hash/count/source/smoke/time fields against the committed snapshot.

Publication is a PostgreSQL transaction: candidate ready/receipt, ingestion success and the serving pointer either commit together or roll back together. The pointer CAS compares both the prior active UUID and activation revision. A late concurrent candidate remains validated/ready with a superseded publication state; it cannot silently replace a newer current. Explicit activation/rollback increments the revision, preventing V1→V2→V1 ABA. Old ready versions are retained.

Failed/canceled candidates do not downgrade a usable current. Source revision/dirty state is separate from serving availability; an upload, source deletion or failed rebuild does not remove a valid current from fill options. An archived KB is unavailable. Already validated completion is idempotent and independent of today's current pointer, so a later rollback never causes a successful version to be rebuilt or reactivated by a worker retry.

## Frozen fill inputs and evidence

Fill creation locks target/global KBs in UUID order and freezes both scopes and the template object in the same transaction as the run, pins and worker job. The existing owner/admin/workspace permission boundaries remain enforced. This implementation requires both scopes to use the same collection and explicitly rejects different collections.

The template materializer reads its pinned object key and verifies hash/size. The worker passes the frozen collection and `--index-scopes` file to Python; it never resolves current again when retrying. Python freezes the normalized scopes in the acquisition fingerprint. Changing pins rejects resume before model calls or prior artifact replacement.

Schema and value queries apply exact namespace/KB/version pairs before top-k and return the actual Qdrant point ID. New versioned evidence cannot enter an unversioned compatibility query. Empty/invalid/extra pins, cross-collection scopes, hidden conflicting metadata and foreign injected hits fail closed. Incoming and outgoing retrieval packs, checkpoint authorities, final field results and the writeback entrypoint are rechecked. A local injected callback cannot change a V1 authority into V2 and still write the workbook.

New manifest 1.3 runs optionally extend the existing contract with `index_scopes`, an archived `index_scopes.json`, and acquisition `index_scope_contract={version:pinned-index-scopes-v1,scopes:...}`. Validator checks the scope file, manifest, frozen fingerprint and every authority hit agree. Historical manifests without this claim retain their established compatibility checks. Go retains raw scope/snapshot fields and verifies a subprocess result against scope bytes captured before process execution. Addressable refs and writeback policy remain unchanged.

Standalone legacy `writeback` still validates its supplied references against its supplied authority; it is not a serving-version resolver. The end-to-end pinned fill contract belongs to the production fill runner and its frozen artifacts.

## Queue recovery and cancellation

Creation transactions insert durable job rows without enqueueing an uncommitted task. Commit is followed by dispatch. Redis failure preserves created/queued rows and the already committed run/build identity. A worker dispatcher scans all pending rows by a cutoff and a `(created_at,id)` keyset; it does not starve jobs behind the first 500. Stable Redis TaskIDs suppress duplicate delivery.

After publication, recovery checks durable completion before rebuilding, consuming another attempt or processing cancellation. A job-state write failure can be repaired from the succeeded ingestion/validated publication even when the job row is still running, failed or was canceled late. Both normal completion and interrupted startup recovery apply this rule.

Cancellation and publication share the KB→version/ingestion→job lock order through the narrow build store. Typed ingestion cancellation and generic associated worker-job cancellation use the same decision: cancellation first prevents publication; publication first rejects a late cancellation. A domain cancellation event is emitted after commit. Lifecycle errors propagate; ready is emitted only after a successful publication commit.

There is no event outbox. A crash between the commit and event persistence can omit a notification; persisted state is authoritative. No exactly-once event delivery is claimed.

## Migration and retention

Historical SQL 1–12 is byte-frozen. A ledger-aware bootstrap uses an advisory transaction lock, recorded checksums and all-or-nothing migration batches. Fresh databases execute each Up once. A complete compatible final-v12 legacy schema is adopted after catalog/ownership checks, then receives forward migration 13; historical seeds are not replayed. Partial/unknown/modified histories and incompatible schemas are rejected without a half-written ledger.

Migration 13 adds version validation/publication state, source revisions, ownership constraints, immutable snapshots and source/template/index pins. Legacy ready versions are labeled `legacy_unversioned + legacy_declared_ready`, not vector-validated. Explicit legacy reading requires matching KB/namespace/collection and an empty physical version field; missing/mismatched old data yields no evidence and requires a new build.

First deployment must stop the old API/worker before replacing them. Old binaries ignore the ledger and still replay historical seeds; mixed-version rolling deployment is unsupported. Migration cannot reconstruct user choices previously overwritten by an old seed replay.

There is no GC implementation in this phase. Old points, ready versions and source pins are retained for rollback and queued fill runs. Pinned source/template objects cannot be physically deleted. This conservative retention can keep failed-candidate inputs; a later retention/GC feature must coordinate exact-scope deletion and pins.

## Verification and limits

Evidence is saved in `artifacts/vnext/phase4b/`; machine-readable final counts and commands are in [PHASE4B_RESULTS.json](PHASE4B_RESULTS.json). The [design record](PHASE4B_DESIGN.md) documents adoption and deployment constraints.

- Python unit and real native CLI/disk-Qdrant tests cover physical V1/V2 coexistence, exact candidate cleanup, build failure injection, snapshot/source integrity, pin filtering, callback mutation, resume and archive tampering.
- Real PostgreSQL tests use isolated random databases: fresh/adoption/restart, concurrent migration/build/publication, atomic SQL faults, receipt negative cases, ownership, CAS/ABA, cancellation order, dirty serving and file/pin deletion races. PG receipts are explicit metadata fixtures; those tests do not substitute for Python's real Qdrant validation.
- Worker tests use FakeRunner to isolate dispatch/publication/error/collection/template contracts. Their scope is separate from native Python/Qdrant tests.
- Required `make test-postgres` and CI PostgreSQL 16.4 service prevent absent database coverage from being reported as successful integration.

No external model call or clean Docker upload→fill→download acceptance has occurred in this phase. Model quality, three fresh Docker datasets, challenge/old141 and A0–A3/A4 evaluation remain Phase 5 work.
