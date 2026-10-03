# Phase 1B — Runtime uploaded form fields

The upload template now determines this run's fields without a shared Step12 artifact. `parse-form-template --template input.xlsx --out form_items.jsonl` exposes the deterministic parser independently. Run priority is explicit `--form-items`, uploaded `--template`, then an explicitly warned legacy fallback. Explicit invalid inputs fail instead of falling back.

Parsing identifies labeled question/target columns, merged headers/categories and multiple worksheets. Ambiguous or changing layouts stop with diagnostics; formula targets are skipped. Target cells include quoted sheet names so equal coordinates on different sheets remain distinct. Existing target answers are excluded by default and can only be collected as heldout values when judge is explicitly enabled. A single byte buffer feeds workbook parsing and the template hash.

Each run archives `form_items.jsonl`, `form_parse_report.json` and `form_input_snapshot.json`. The snapshot hashes the actual canonical JSONL, template bytes, parser version, selected field identities, row selection, target/global namespaces and room context. Resume validates it before opening prediction, overlay or trace checkpoints, and before replacing run inputs. Missing or changed snapshots stop the run. Historical provenance-only resume tests now explicitly seed a proven input snapshot; absence of a provenance checkpoint still cannot fabricate native quotes.

Go always materializes and passes the template, including when writeback is off. Materialization checks the stored SHA256 before atomic file publication and preserves the earlier file on a mismatch. Defaults use all parsed fields. Progress reads actual field counts from Python `run_started`, so multi-sheet rows and dynamic field counts are measured correctly.

## Verification

- Python full suite: 362 passed, no failures, errors or skips.
- Go `go test ./...`: passed.
- Ruff and diff whitespace checks: passed.
- The fresh-upload Phase0B regression passes; Phase0A remains green.
- 22 integration cases run the actual CLI, actual disk Qdrant, parser, runner, checkpoint and artifact path, with localhost embedding/rerank/chat substitutes.
- Cases include input priority, renamed/reordered templates, changed target columns, multiple sheets, first rows and rows beyond 144, disabled writeback, ambiguous/invalid input, stable resume, changed template/items/parser/selection/scope and unchanged earlier artifacts on rejection.
- The original 141-field template parses directly to its 141 G4–G144 targets; no original template is modified.
- Commands, logs and JUnit XML are in `artifacts/vnext/phase1b/`. Machine-readable results are in `PHASE1B_RESULTS.json`.

This verifies engineering contracts with model substitutes. It does not claim real-model quality, a clean API→queue→container upload chain, addressed confirmed writeback, or completion of the later phases.
