# Nested Doc RAG for Gongkan Form Filling

This project implements a Python core for datacenter gongkan form filling: it ingests complex nested Office knowledge files into a traceable Qdrant index, then uses Step15AgentRunner to fill survey forms with layered RAG, answer arbitration, review routing, and safe Excel writeback.

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
- Agent overlay is additive and provides trace, checkpoint/resume, critic flags, review routing, reference enrichment suggestions, and writeback gating

## Recommended Runtime

Use `Step15AgentRunner` overlay mode for production-oriented gongkan form filling.

Step 15 raw answer arbitration is the effect engine. Agent overlay is the production control layer. Evaluation uses raw predictions. Review and writeback use raw predictions plus overlays. Overlay never mutates raw `answer_status` or `answer_value`.

Runtime choices:

- `Step15AgentRunner`: recommended production-oriented runtime.
- `FieldFillingAgent`: strict/ablation runtime, including offline mini mode and the v1.2 selected/reference gate.
- Step 15 legacy scripts: historical/debug compatibility entry points.

## Core Workflows

### 1. Knowledge ingestion and indexing

Current main chain:

```text
01_file_registration
02_datacenter_routing
03_format_probe
04a_structure_parse
04b_embedded_object_parse
05_segment_extract
06_segmentation_audit
07_agent_need_audit
08_llm_structure_hint
09_table_candidate_resolution
10_semantic_segment_audit
11_embedding_build
15_vector_store / Qdrant index
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
-> review queue
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
- Qdrant local path or server configuration

Service URLs belong in `config/local.yaml`, environment variables, or CLI flags. API keys are read through environment variables such as `DEEPSEEK_API_KEY`. Do not commit `config/local.yaml`, `.env`, generated artifacts, vector stores, source data, or secrets.
