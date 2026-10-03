# Phase 1 — Unified evidence and retrieval

Implementation starts after the preserved baseline commits `470c5ea` and `3a5a799`, on `feat/evidence-grounded-vnext` in the isolated worktree. The original workspace remains unchanged across all 103 frozen file hashes.

Native Excel, Word and text records now use five physical evidence kinds. Original source identity, namespace, corpus policy, attachments and provenance remain available. Excel headers, empty answers, formulas and images stay ordinary table rows; clear label/value pairs carry their exact cell address. Merged context is recorded separately from the native row text. Structured rows remain intact to avoid attaching a complete value to an incomplete text fragment. Word preserves block order, heading paths and paragraph/table/row indices. Native parsing is versioned `native-office-v2`.

Default, Docker and local-example retrieval use the same kind-based five-layer plan. Old records lacking a kind use an explicit source allowlist, emit a compatibility warning and are checked again after kind normalization. Read compatibility does not rewrite Qdrant points. Canonical records do not inherit the legacy source restriction; explicit source-specific actions still constrain both paths. Actual Qdrant and injected retrieval reject empty constraints and isolate target/global namespaces. Table details include raw-text rows so embedded native table fragments remain reachable; global intro table rows remain low-priority global details.

A physical kind or layer name does not grant answer trust. Located label/value evidence still needs a matching field, nonempty value and grounding. Conflicting canonical structured values are preserved as conflicts instead of privileging a historical source-type label. Existing source-only custom plans remain supported.

## Verification

- Python: 308 passed, 0 failed, 0 errors; four expected legacy compatibility warnings.
- Ruff: passed for every changed Python file.
- Regression A: actual native upload → actual local Qdrant → Docker production plan passes.
- Boundary coverage includes zero/False, merged header/category, empty/formula/image cells, image-only unavailable references, original CRLF/Unicode/hash space, parent versus native addresses, Word block order, legacy metadata roundtrip and retrieval scope/source constraints.
- No external model calls. Go production code is unchanged in this phase.
- The sole excluded regression is the runtime form-template parsing case owned by Phase 1B; this result does not represent completion of vNext.

Commands, logs and JUnit XML are in `artifacts/vnext/phase1/`; machine-readable results are in `PHASE1_RESULTS.json`.
