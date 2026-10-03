# Phase 4 — Explicit policies for existing workbook values

Confirmed answers now preserve existing ordinary target values by default. `writeback.existing_value_policy=preserve` is the dataclass and all shipped YAML profiles' default. Both `run-step15-agent` and standalone `writeback` expose `--existing-value-policy preserve|overwrite_confirmed|overwrite_all`. The policy is independent of legacy `mode=safe|overwrite`: `mode=overwrite` alone cannot grant permission to replace a value and emits an explicit compatibility warning.

## Policy and source gates

- `preserve`: any nonempty target is unchanged, enters review with `reason=target_non_empty`, `writeback_action=skipped_non_empty_cell` and `WB_TARGET_NON_EMPTY`. Empty confirmed targets can still be written. A cell is empty only when its value is `None` or `""`; whitespace strings, numeric zero and other existing values are preserved.
- `overwrite_confirmed`: an existing ordinary value can be replaced only for a confirmed result that passes source authority checks. Rejected existing targets use `WB_OVERWRITE_POLICY`; empty targets retain their existing status/uncertainty gates.
- `overwrite_all`: available only through an explicit CLI option, recorded as `overwrite_all_cli=true`. It allows existing ordinary values through the nonempty-cell policy check, while retaining formula protection, answer/status validation, independent typed evidence/authority checks and the separate `allow_uncertain` gate. It cannot force a partial/flagged answer or an unresolvable reference to write.

YAML/environment configuration accepts only `preserve` or `overwrite_confirmed`. CLI explicitly applies `overwrite_all` to the effective configuration. Runner and writer reject it without the corresponding explicit CLI marker before workbook execution. Invalid policies also fail instead of silently selecting a permissive mode.

Formula protection runs before the existing-value policy. All three policies preserve formula cells with `skipped_formula`. Invalid/merged targets and duplicate targets retain their protections. Policy-rejected cells retain their original value, style and human comments; new confirmed writes continue to archive resolved evidence and use the existing Evidence worksheet. Runtime writes a separate `filled_form.xlsx`; the tested input workbooks remain byte-for-byte unchanged.

The raw model answer remains separate from the actual workbook action. Preserving a human value does not replace the generated answer with the human value, and existing template answers are not sent as heldout/model input in production `--no-judge` runs. Review and audit records explain why a supported answer was not written.

## Audit, manifest and resume

Every writer audit records `old_value`, the actual post-action `new_value`, and `policy`, alongside the existing desired `answer_value`, action/status/reason, error code, source IDs and evidence references. A skipped action has `new_value=old_value`; the proposed answer is never presented as a successful write. Native dates and formula objects use explicit JSON-compatible audit representations.

New runs emit manifest **1.3**. Its writeback section archives the effective `existing_value_policy` and `overwrite_all_cli` marker. The independent artifact validator checks the policy against the frozen input contract, matches audit fields, verifies permitted existing-value changes and protected formulas, and checks actual output workbook values against the audit. A permissive raw answer/overlay, an altered policy or fabricated successful action cannot replace these checks. Existing manifest 1.2 fixtures remain supported without retroactively requiring Phase 4 policy fields; older evidence compatibility rules remain unchanged.

`old_value` is the writer's record of its workbook read. Validator checks independently establish final-file/policy consistency; they do not certify an unforgeable historical old value.

The acquisition fingerprint adds `writeback_policy={version: writeback-policy-v1, existing_value_policy, overwrite_all_cli}`. Changing policy or its explicit all-origin identity rejects resume before checkpoint parsing, model calls or replacement of prior artifacts. This is part of the same proven template/input/retrieval fingerprint rather than a mutable output preference.

## Verification and remaining acceptance

Thirteen new runtime integration cases execute real native XLSX ingestion, disk Qdrant, `run-step15-agent`, standalone `writeback` and `validate-artifacts` CLIs with production parsing, writer, checkpoints and artifact validation. They verify default preservation of a unique human sentinel, whitespace and zero without human-value leakage; confirmed empty-cell writing; explicit confirmed/all replacement of ordinary values; three-policy formula protection through standalone writing; all-mode refusal with missing authority or partial status; actual old/new/policy audit values; and policy-changing resume rejection without model calls or byte changes to earlier artifacts. The three authorized manifest/provenance/addressed-evidence regression files pass with their new production-manifest 1.3 assertions.

Only embedding/rerank/chat HTTP endpoints are deterministic localhost substitutes. These cases prove workbook policy and failure contracts, not real-model accuracy or clean Docker/API upload acceptance. Phase 3/3B retrieval behavior is retained. Knowledge-base version activation and real fresh/challenge/model evaluation remain later phases; this report does not claim an old141 model rerun.

- Python full suite: **700 passed**, zero failures/errors/skips, **120.17 seconds**. Four expected warnings exercise explicit legacy evidence fallback.
- Full JUnit records **47** new writer-policy cases, **16** policy/artifact consistency and tamper cases, and the **13** new runtime integration cases. Related regression runs are not counted as newly added cases.
- Go `go test ./...`, Ruff across `src`/`tests`, and diff whitespace checks: passed. The original manifest/provenance/addressed-evidence group also passed its targeted 28-case run.
- All **103** frozen original-workspace files have unchanged hashes. External model calls and clean Docker E2E runs: **zero**.

Evidence is in `artifacts/vnext/phase4/`: `python.log`, `python.xml`, `go.log`, `ruff.log`, targeted checks, `commands.json` and `original-workspace-integrity.json`. Machine-readable results are in [PHASE4_RESULTS.json](PHASE4_RESULTS.json). [ACCEPTANCE.md](ACCEPTANCE.md) records W01's verified engineering contract and the remaining real-model/Docker requirements; [../contracts.md](../contracts.md) documents manifest 1.3 alongside older versions.
