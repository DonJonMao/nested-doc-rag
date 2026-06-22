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

Example:

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

Writeback artifacts are present only when writeback is enabled and completed:

- `filled_form.xlsx`
- `writeback_audit.jsonl`
- `evidence_map.json`
- `image_evidence.jsonl`, when fields reference `proof_attachment_ids`

Field writeback statuses:

- `confirmed`: safe answer, normal writeback.
- `uncertain`: evidence exists but manual verification is still needed. Written only when `writeback.allow_uncertain=true` and the target cell is empty; otherwise exported for review only.
- `flagged`: never written automatically.

Allowed writeback actions:

- `written`
- `written_red_comment`
- `review_only`
- `skipped_uncertain_policy`
- `skipped_non_empty_cell`
- `skipped_formula`
- `invalid_cell`
- `duplicate_target_cell`

AgentScope MAS is the default Step15 runtime. The MAS roles are deterministic wrappers around the original Step15 query,
retrieval, answer arbitration, critic, and overlay control functions. They do not change the stable artifacts above.

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
