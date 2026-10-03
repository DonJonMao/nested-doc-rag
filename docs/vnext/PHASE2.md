# Phase 2 — Addressable evidence and independent writeback verification

The model now chooses evidence IDs and optional literal quotes. File, worksheet, table, row, cell, paragraph, native text and attachment identity are resolved from this field's actual retrieval pack. Model-supplied filenames, object keys, coordinates and attachment metadata cannot override source metadata. `FieldPrediction.evidence_refs` contains typed references; original source IDs and reference documents remain compatible outputs.

Resolution preserves full original text, UTF-8 hashes, CRLF, Unicode, source chains, index identity and proof attachments. Native text without a declared hash can be hashed directly. Legacy `raw_text` is usable as original text only with an explicit same-space hash that verifies; compressed or embedding text cannot silently become original text. Invalid IDs, addresses, ownership scopes or attachments produce stable EV diagnostics. A confirmed answer needs a nonempty, locatable reference. The raw answer/status remains unchanged when the overlay blocks writing.

`retrieval_evidence.jsonl` archives per-field authority as `{field_id, top_hits}`. Every checkpoint persists its corresponding authority before predictions. Resume requires the new evidence contract fingerprint, rechecks saved refs and overlays, and rejects missing or altered authority before contacting a model. Historical artifacts remain readable; a checkpoint without authority cannot be upgraded into a new confirmed run.

The Excel writer independently validates typed refs against the field's hits, selected source IDs and selected attachments. An externally supplied `verified=true`, permissive overlay or source ID alone cannot authorize confirmed writing. Manifest 1.2 requires authority and cross-checks raw, audit and manifest refs. Rejected legacy display clues remain review diagnostics, rather than being certified as typed evidence. Manifest 1.0/1.1 retains explicit `legacy_read` validation.

Production defaults now use the existing Evidence sheet for field/answer/status/source/location/text and thumbnails after the text rows. Main-form comments point to the actual Evidence location. An existing user worksheet named Evidence is preserved; only a positively marked generated sheet can be regenerated, and naming collisions use a new sheet. The existing nested quote provenance stays intact for Go/UI, with typed refs also archived separately as `addressed_evidence_refs`. Review items carry typed prediction refs. Go's current detail view still consumes its existing provenance fields; this phase does not claim a complete new address UI.

## Verification

- Python full suite: **476 passed**, zero failures/errors/skips. Four expected warnings exercise explicit legacy retrieval fallback.
- Go `go test ./...`: passed. Ruff across `src` and `tests`, and diff whitespace checks: passed. Two inherited unused/import-order lint issues were also removed without runtime changes.
- Seven added integration cases use real native XLSX ingestion, actual disk Qdrant, a separate real CLI process, the form parser, writer, checkpoints and artifact validator. Only localhost embedding/rerank/chat services are deterministic substitutes.
- Valid evidence writes; forged model coordinates are ignored; unknown IDs, missing native source and nonexistent attachments block writing. Missing authority and changed saved refs reject resume without model calls or changes to prior outputs.
- Independent writer/artifact cases cover identity/address/hash/attachment tampering, per-field authority, image metadata, user Evidence sheet protection and safe regeneration.
- The exact historical 141-field/writeback-37 run passes `legacy_read` through the actual validator CLI. This is historical compatibility verification, not a current model rerun.
- All **103** frozen original-workspace files remain unchanged.

Logs, JUnit XML, test-generated workbooks and original-workspace integrity results are in `artifacts/vnext/phase2/`; machine-readable results are in `PHASE2_RESULTS.json`. Both Phase0 upload regressions remain green. External model calls: zero. Clean Docker E2E and real-model accuracy remain later-stage requirements. This phase has not yet changed the legacy retrieval decision mechanism or added nonempty-cell/version publication policies.
