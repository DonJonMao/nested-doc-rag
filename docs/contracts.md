# Artifact Contracts

Go/backend integrations should depend on these artifacts, not on Python internal functions. Paths in `run_manifest.json` are relative to the run `out_dir`.

## `predictions_raw.jsonl`

Purpose:

- raw Step 15 answer arbitration result
- used for evaluation
- immutable in Step15AgentRunner overlay mode

Fields:

- `field_id`
- `row_index`
- `target_cell`
- `question_text`
- `answer_value`
- `answer_status`
- `confidence`
- `source_chunk_ids`
- `evidence_attachment_ids`
- `reference_chunk_ids`
- `reference_source_documents`
- `reference_snippets`
- `method_name`
- `validation`

## `predictions.jsonl`

Purpose:

- compatibility alias of `predictions_raw.jsonl`
- must be identical to raw unless explicitly documented otherwise

## `agent_overlays.jsonl`

Purpose:

- Agent production control layer
- never mutates raw prediction

Fields:

- `field_id`
- `row_index`
- `target_cell`
- `critic_flags`
- `review_required`
- `writeback_allowed`
- `suggested_status`
- `suggested_answer_value`
- `suggested_reference_source_documents`
- `suggested_reference_chunk_ids`
- `suggested_reference_snippets`
- `risk_level`
- `reasons`

## `predictions_agent_view.jsonl`

Purpose:

- human-friendly merged view
- raw prediction plus overlay

## `review_items.jsonl`

Purpose:

- offline manual completion list
- contains partial, not-found, conflict, failed, risk-flagged, and uncertain fields that need offline manual completion or verification
- may include `writeback_status`, `writeback_action`, `evidence_refs`, and `error_code`

## `trace.jsonl`

Purpose:

- field-level execution trace
- records query planning, layered retrieval, answer arbitration, overlay construction, critic flags, review routing, checkpointing, failures, and retries

## `trace_summary.json`

Purpose:

- run-level event and status summary
- includes raw status counts, overlay counts, critic flags, latency summaries, failures, and resume counters

## `run_summary.md`

Purpose:

- human-readable run report

## `summary.json`

Purpose:

- judge/evaluation metrics if judge is enabled
- production overlay metrics if judge is disabled

## `run_manifest.json`

Purpose:

- stable machine-readable run index for Go/backend integrations

Historical manifest 1.1 example (retained for compatibility):

```json
{
  "schema_version": "1.1",
  "run_id": "step15_agent_...",
  "created_at": "2026-06-08T07:36:41Z",
  "finished_at": "2026-06-08T09:01:41Z",
  "status": "completed",
  "engine": "step15_agent_overlay",
  "target_namespace": "xixian_4",
  "global_namespace": "global",
  "room_context": "西咸4号楼 301机房",
  "rows": "4-144",
  "judge_enabled": true,
  "writeback_enabled": true,
  "artifacts": {
    "predictions_raw": "predictions_raw.jsonl",
    "predictions": "predictions.jsonl",
    "agent_overlays": "agent_overlays.jsonl",
    "predictions_agent_view": "predictions_agent_view.jsonl",
    "review_items": "review_items.jsonl",
    "trace": "trace.jsonl",
    "trace_summary": "trace_summary.json",
    "run_summary": "run_summary.md",
    "summary": "summary.json",
    "filled_form": "filled_form.xlsx",
    "writeback_audit": "writeback_audit.jsonl",
    "evidence_map": "evidence_map.json",
    "image_evidence": "image_evidence.jsonl"
  },
  "counts": {
    "total_fields": 141,
    "answered": 50,
    "partial_clue": 72,
    "not_found": 18,
    "conflict_unresolved": 0,
    "review_required": 92,
    "writeback_allowed": 49,
    "failed": 1
  },
  "writeback": {
    "summary": {
      "confirmed": 82,
      "uncertain": 11,
      "flagged": 48,
      "written": 89,
      "review": 59
    },
    "fields": [
      {
        "field_key": "row_25_power_supply",
        "field_id": "row_25_power_supply",
        "row_index": 25,
        "target_cell": "Sheet1!D25",
        "sheet_name": "Sheet1",
        "cell": "D25",
        "status": "uncertain",
        "answer_status": "partial_clue",
        "answer_value": "双路市电",
        "writeback_action": "written_red_comment",
        "evidence_refs": [
          {
            "document_id": "doc_123",
            "object_key": "kb/xixian_4/docs/capability.xlsx",
            "qdrant_point_id": "pt_987",
            "source_type": "main_excel_capability",
            "source_anchor": "能力清单!H42",
            "sheet_name": "能力清单",
            "cell": "H42",
            "image_object_key": "runs/fill_001/evidence/row_25_power_supply/proof_1"
          }
        ]
      }
    ]
  }
}
```

Allowed `status` values:

- `completed`
- `completed_with_failures`
- `failed`

### Optional original-text evidence

New runs add `artifacts.evidence_provenance: "evidence_provenance.jsonl"` and an `evidence` block with `summary` and `fields`. This is an additive extension to manifest version 1.1; old manifests without it remain valid. The JSONL contains one field record per line, matching `evidence.fields`.

Field records include the field identity, question, original answer/status, target cell, final writeback status/action, gate reasons, and `evidence_refs`. Each reference contains source metadata from the actual retrieved hit and a `provenance` object:

- `match_status`: `exact`, `ambiguous`, `unmatched`, or `unavailable`.
- `quote`, `source_text`, `source_text_hash`, `text_space`, `index_version`, and a diagnostic `reason`.
- `start` and `end`: non-null only for `exact`; a half-open interval `[start,end)` counted in Unicode code points within the source chunk. CR and LF count separately. Exact matching requires a unique verbatim quote in `raw_source_text` or legacy `raw_text`.

`source_text_hash` is the SHA256 of UTF-8 source text as 64 hexadecimal characters; a missing source text has an empty hash. A reference's `source_document_hash` identifies the complete source file and is independent of this chunk hash. Missing index versions are recorded as `unknown`. The four summary counts count references, not fields.

Final actions come from the Excel writeback audit when available. Quotation location does not establish semantic support and does not change `predictions_raw.jsonl`, Overlay decisions, or writeback gates. Go validates declared artifacts, hashes, counts, text spaces and exact intervals before exposing the archived records; old runs have an empty evidence list in the detail API. The Python engine run identifier and the Go fill-run UUID belong to separate identity spaces.

Writeback artifacts are present only when writeback is enabled and completed:

- `filled_form.xlsx`
- `writeback_audit.jsonl`
- `evidence_map.json`
- `image_evidence.jsonl`, when fields reference `proof_attachment_ids`

Knowledge ingestion and Step 11 manifest build also produce `proof_attachment_registry.jsonl` plus `evidence_images/` when source documents contain image proof attachments.

Field writeback statuses:

- `confirmed`: the answer passed the confirmation gates; the actual `writeback_action` also reflects formula and existing-value policy checks.
- `uncertain`: evidence exists but manual verification is still needed. Written only when `writeback.allow_uncertain=true` and the effective existing-value policy permits it; otherwise exported for review only.
- `flagged`: never written automatically.

When a value is written to the workbook, the target cell comment uses a compact evidence format: source document name plus the recalled text block. Uncertain comments use the same format with the configured `[UNCERTAIN]` prefix. Image proof references stay out of target cell comments. Knowledge ingestion and Step 11 manifest build extract workbook image proofs into stable `evidence_images/` files and `proof_attachment_registry.jsonl`; writeback resolves `attachment_id` through that registry and appends image proofs at the end of the `Evidence` sheet. Existing old indexes still fall back to source workbook `DISPIMG` media lookup.

Allowed writeback actions:

- `written`
- `written_red_comment`
- `review_only`
- `skipped_uncertain_policy`
- `skipped_non_empty_cell`
- `skipped_formula`
- `invalid_cell`
- `duplicate_target_cell`

Production Step15 defaults to sufficiency-guided retrieval with `agentscope.mode=off`. Legacy AgentScope/MAS runtimes
require an explicit opt-in with sufficiency disabled. Their diagnostic roles do not change the stable artifacts above.

Step15 production retrieval uses the dense layered retrieval plan. `summary.json` and `run_manifest.json` record
`retrieval_plan: layered`; alternate Step15 flat, lexical, or fused retrieval paths are not production artifacts.

Diagnostic MAS/AgentScope trace artifacts may be present when `agentscope.mode` is `equivalent_mas` or `trace_only`:

- `mas_trace.jsonl`
- `agentscope_events.jsonl`

These are diagnostic artifacts only. Go/backend integrations must not require them and must continue to depend only on the stable artifacts listed above.

Grounding may add an optional diagnostic artifact:

- `grounding_trace.jsonl`

This file is an optional trace output. It does not change the schema or semantics of `predictions_raw.jsonl`,
`agent_overlays.jsonl`, `review_items.jsonl`, or the writeback artifacts.

## vNext addressable evidence (manifest 1.2)

`FieldPrediction.evidence_refs` is an additive typed list derived from the current field's retrieval hits. It preserves source identity, physical address, full native text, its hash/text space, index identity, source chain and real attachments. Legacy source IDs and reference documents remain available. The answer model supplies selected IDs, quote and reason; system metadata supplies file and location.

`retrieval_evidence.jsonl` contains `{field_id, top_hits}` rows and is required by manifest 1.2 validation. Confirmed writeback independently verifies typed refs against this authority and the raw prediction's selected source/attachment IDs. Rejected display clues may remain in review, with explicit diagnostics; they do not authorize confirmed writing. Old manifest 1.0/1.1 artifacts are validated as `legacy_read`.

The existing `manifest.evidence.fields[].evidence_refs[].provenance` shape and Unicode codepoint offsets remain unchanged for Go/UI. Typed refs are additionally stored as `addressed_evidence_refs` in these fields and as `evidence_refs` in raw predictions and agent review items. New runs also persist authority checkpoints and an evidence contract version in the form input fingerprint; old checkpoints without this proof require a new run directory.

## vNext existing-value policy (manifest 1.3)

New Step15 runs emit manifest 1.3, extending 1.2's strict addressed-evidence/authority validation. Manifest 1.2 retains that strict address contract without requiring the new policy fields; 1.0/1.1 retain `legacy_read` compatibility.

`form_input.acquisition_contract.writeback_policy` freezes `version=writeback-policy-v1`, `existing_value_policy` and `overwrite_all_cli`. The effective policy in `writeback.config.existing_value_policy` and CLI-origin marker in `writeback.overwrite_all_cli` must agree with this snapshot. Production defaults to `preserve`; `overwrite_all` requires the explicit CLI option and a true frozen origin marker. A policy change requires a new run directory.

Each `writeback_audit.jsonl` row and corresponding `writeback.fields` entry must agree on `old_value`, actual post-action `new_value` and `policy`, in addition to existing field/action/status/evidence data. `answer_value` remains the proposed answer. A skipped action retains the old value; it cannot record the proposed answer as if writing succeeded.

The validator independently verifies formula protection, preservation of nonempty targets under `preserve`, confirmed-only existing-value replacement under `overwrite_confirmed`, and explicit selection of `overwrite_all`. Source/status gates still apply. It also reads the actual `filled_form.xlsx` target values and compares them with the audit. `old_value` is recorded by the writer when it reads the workbook; these checks establish final-file/policy consistency, not an independently unforgeable history of the prior cell value.

## vNext immutable knowledge scopes

Candidate builds use the existing knowledge index version UUID as physical identity. Worker ingestion accepts `--index-version-id`, `--input-snapshot` and the SHA256 of the exact snapshot bytes. `kb-build-input-v1` and `kb-index-validation-v1` are defined centrally in `go-server/internal/knowledge/protocol.go`; the receipt is produced only after scoped native count/smoke and source verification. The successful ingestion manifest archives raw `input_snapshot.json` and `validation_receipt.json`.

Production fills pin target/global `{collection, namespace, knowledge_base_id, index_version_id, storage_contract}` when the run is created. Both scopes must use one collection; different collections are explicitly rejected. `--index-scopes` and the frozen collection are passed to Python without resolving current at retry time. The `pinned-index-scopes-v1` acquisition contract enters the form-input fingerprint. Manifest 1.3 optionally adds `index_scopes` and the matching `index_scopes.json` artifact; historical 1.3 artifacts without this claim retain compatibility. Validator checks pins, fingerprint and hit authority agree.

Physical `point_id`/`qdrant_point_id` reflect the returned Qdrant point. Schema/value queries apply exact paired scopes before top-k; empty or invalid pins cannot broaden retrieval. Explicit legacy pins require matching KB/namespace/collection and no physical version UUID. A legacy declaration does not certify that its old points exist.

Knowledge publication and cancellation use narrow PostgreSQL transactions and UUID+activation-revision CAS. Old ready points/versions are retained. Ledger migration adopts only a compatible complete final-v12 schema and never replays historical seeds on restart. Stop old API/worker before the first ledger-aware deployment. Details and measured limits are in [vNext Phase 4B](vnext/PHASE4B.md).
