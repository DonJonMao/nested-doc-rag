# Nested Doc RAG for Gongkan Form Filling

This project implements a Python core for datacenter gongkan form filling: it ingests complex nested Office knowledge files into a traceable Qdrant index, then uses Step15AgentRunner to fill survey forms with layered RAG, answer arbitration, offline review item export, and safe Excel writeback.

## What This Project Does

### Knowledge Base Ingestion

The ingestion pipeline turns complex datacenter Office files into retrievable, auditable knowledge:

- parse nested Excel and Word files
- preserve workbook, table, row, paragraph, and embedded-object structure
- build semantic segments instead of fixed-length chunks
- embed segment text with a configured embedding service
- store vectors and metadata in Qdrant

Important payload fields include `namespace`, `source_type`, `corpus_layer`, `raw_text`, `text_for_embedding`, `source_anchor`, and `proof_attachment_ids`.

### Gongkan Form Filling

The form-filling runtime uses `Step15AgentRunner` overlay mode:

- Step 15 layered RAG is the effect engine
- the LLM sees the full layered evidence pack and arbitrates `answered`, `partial_clue`, `not_found`, or `conflict_unresolved`
- raw prediction is immutable and used for evaluation
- Agent overlay is additive and provides trace, checkpoint/resume, critic flags, review item generation, reference enrichment suggestions, and writeback gating

## Recommended Runtime

Use `Step15AgentRunner` overlay mode for production-oriented gongkan form filling.

Step 15 raw answer arbitration is the effect engine. Agent overlay is the production control layer. Evaluation uses raw predictions. Review and writeback use raw predictions plus overlays. Overlay never mutates raw `answer_status` or `answer_value`.

Runtime choices:

- `Step15AgentRunner`: recommended production-oriented runtime.
- `FieldFillingAgent`: strict/ablation runtime, including offline mini mode and the v1.2 selected/reference gate.
- Step 15 legacy scripts: historical/debug compatibility entry points.

## Core Workflows

### 1. Knowledge ingestion and indexing

This branch keeps the maintained Python package entry points under `src/nested_doc_rag` and removes the historical numbered step directories. Ingestion/indexing code lives in:

```text
src/nested_doc_rag/parsing
src/nested_doc_rag/segmentation
src/nested_doc_rag/embedding
src/nested_doc_rag/form
src/nested_doc_rag/retrieval
```

The pipeline does not rely on fixed-length chunks. Excel row-level capability items are primary segments. `raw_text` is used as evidence, `text_for_embedding` is used for embedding, `source_anchor` supports source tracing, and `proof_attachment_ids` supports audit/review. Qdrant payloads preserve `namespace`, `source_type`, `corpus_layer`, and `source_anchor` so retrieval results remain explainable.

### 2. Gongkan form filling

`Step15AgentRunner` flow:

```text
field
-> masked query
-> room_context
-> layered Qdrant retrieval
-> rerank
-> Step 15 answer arbitration prompt
-> raw prediction
-> Agent overlay
-> review_items artifact for offline manual completion
-> optional safe Excel writeback
```

The LLM sees the layered evidence pack and decides the answer status. The overlay does not change the raw answer. Excel writeback is gated by `overlay.writeback_allowed`, so unsafe or review-needed rows are not written automatically.

## Quickstart

Install:

```bash
python -m pip install -e .
```

Run tests:

```bash
pytest
```

Inspect configuration:

```bash
python -m nested_doc_rag.cli show-config --config config/local.example.yaml
```

## Gongkan Platform App

The productized Gongkan platform lives alongside the Python Core:

- `go-server/` provides the API server, auth/RBAC, workspace/file/artifact services, jobs, worker orchestration, SSE, knowledge-base ingestion APIs, fill-run APIs, and OpenAPI.
- `web/` provides the Vue 3 app for role-based login, admin knowledge management, user form upload, fill-run creation, SSE progress, task history, result summaries, and artifact downloads.

Current product MVP is a download-oriented automatic form-filling flow. The system only writes fields that pass grounding and the writeback gate (`writeback_allowed=true`) into `filled_form.xlsx`. Unsafe, evidence-insufficient, or review-needed fields are not written automatically; they are exported through `review_items`, and users complete those fields offline after downloading the workbook.

User flow:

1. Create a fill task from a ready knowledge base and an uploaded form template.
2. Wait for the worker to finish Python Step15.
3. Download `filled_form.xlsx`, the safe automatically filled workbook.
4. Download `review_items.csv` to see fields that need offline manual completion.
5. Complete the remaining fields manually outside the platform.

The current MVP does not provide online field approval, online spreadsheet editing, human-review re-writeback, `reviewed_filled_form.xlsx`, or multi-level approval workflow.

The web app calls the real Go API. Long-running ingestion and form-filling tasks are executed by the Go worker through Python Core CLI entry points; the frontend does not mock task success.

Run platform tests and builds:

```bash
cd go-server && go test ./...
cd ../web && npm install && npm run build
```

Run mini baseline smoke tests:

```bash
python -m nested_doc_rag.cli run-baselines \
  --config experiments/form_filling_baselines.yaml \
  --out-dir artifacts/experiments/baselines
```

Recommended real form-filling run:

```bash
# Step15 retrieval plan is layered by default.
python -m nested_doc_rag.cli run-step15-agent \
  --config config/local.yaml \
  --target-namespace xixian_4 \
  --global-namespace global \
  --room-context "西咸4号楼 301机房" \
  --rows 4-144 \
  --prompt-version step15_compat \
  --judge \
  --use-judge-cache \
  --resume \
  --out-dir artifacts/runs/step15_agent_overlay
```

Run with Excel writeback:

```bash
# Step15 retrieval plan is layered by default.
python -m nested_doc_rag.cli run-step15-agent \
  --config config/local.yaml \
  --target-namespace xixian_4 \
  --room-context "西咸4号楼 301机房" \
  --rows 4-144 \
  --prompt-version step15_compat \
  --no-judge \
  --template data/forms/基地云机房信息调研表.xlsx \
  --writeback \
  --resume \
  --out-dir artifacts/runs/step15_agent_writeback
```

Validate an output directory:

```bash
python -m nested_doc_rag.cli validate-artifacts \
  --run-dir artifacts/runs/step15_agent_overlay
```

## Outputs

Stable Step15AgentRunner overlay artifacts:

- `predictions_raw.jsonl`
- `predictions.jsonl`
- `agent_overlays.jsonl`
- `predictions_agent_view.jsonl`
- `review_items.jsonl`
- `trace.jsonl`
- `trace_summary.json`
- `run_summary.md`
- `summary.json`
- `run_manifest.json`
- `filled_form.xlsx`, when writeback is enabled and completed
- `writeback_audit.jsonl`, when writeback is enabled and completed
- `evidence_map.json`, when writeback is enabled and completed

`predictions.jsonl` is a compatibility alias of `predictions_raw.jsonl` in overlay mode. Go/backend integrations should use `run_manifest.json` to locate artifacts and should not depend on Python internal functions.

See [docs/contracts.md](docs/contracts.md) for the frozen artifact contract.

## Remote Services

Real runs require locally configured services:

- embedding endpoint
- rerank endpoint
- DeepSeek/OpenAI-compatible chat completion endpoint
- Qdrant local path or server configuration. Leave `qdrant.url` empty to use the embedded local path under `paths.qdrant_path`; set `qdrant.url` such as `http://localhost:6333` to use a Qdrant service.

Service URLs belong in `config/local.yaml`, environment variables, or CLI flags. API keys are read through environment variables such as `DEEPSEEK_API_KEY`. Do not commit `config/local.yaml`, `.env`, generated artifacts, vector stores, source data, or secrets.
