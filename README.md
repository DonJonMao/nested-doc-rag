# Nested Doc RAG for Datacenter Knowledge

This repository contains an engineering prototype for parsing nested datacenter
knowledge files, building a RAG index, and evaluating closed-book answers for
gongkan survey forms.

## Scope

The repository contains code and design documents only. Local source data,
generated artifacts, vector stores, and evaluation outputs are excluded by
`.gitignore`.

## Quickstart

Install the package in editable mode from the repository root:

```bash
conda activate datacenter
python -m pip install -e .
# For local development:
python -m pip install -e ".[dev]"
```

Inspect the merged configuration:

```bash
python -m nested_doc_rag.cli show-config --config config/local.example.yaml
```

Run tests:

```bash
pytest
```

Run the mini baseline comparison:

```bash
python -m nested_doc_rag.cli run-baselines \
  --config experiments/form_filling_baselines.yaml \
  --out-dir artifacts/experiments/baselines
```

Write predictions back to an Excel form:

```bash
python -m nested_doc_rag.cli writeback \
  --template path/to/survey_form.xlsx \
  --pred artifacts/runs/demo/predictions.jsonl \
  --out artifacts/runs/demo/filled_form.xlsx
```

## Current Structure

Reusable code now lives under `src/nested_doc_rag`:

- `config`: centralized YAML/env/CLI configuration.
- `schemas`: typed data contracts for documents, segments, retrieval,
  evaluation, agents, and Excel writeback.
- `io`, `parsing`, `segmentation`: deterministic file reading and chunk
  preparation utilities.
- `embedding`, `retrieval`: embedding clients, manifests, Qdrant retrieval,
  rerank, and layered retrieval primitives.
- `form`, `evaluation`, `agent`, `excel`: gongkan form analysis, field-level
  metrics, repair scaffolding, and workbook writeback.

Top-level directories are organized around how the package is used:

- `config/`: default and local configuration templates.
- `examples/mini_data/`: small non-sensitive fixtures for tests and smoke runs.
- `experiments/`: reproducible experiment configs, including form-filling
  baseline comparisons.
- `apps/legacy_wrappers/`: compatibility entry points for the historical
  numbered steps while migration continues.
- `01_*` through `15_*`: retained legacy script directories and historical
  artifacts. Treat these as compatibility/debug entry points, not the primary
  API.

## Main Workflows

Configuration inspection:

```bash
python -m nested_doc_rag.cli show-config --config config/local.example.yaml
```

Field-level evaluation:

```bash
python -m nested_doc_rag.cli eval-fields \
  --gold examples/mini_data/gold_fields.jsonl \
  --pred examples/mini_data/predictions.jsonl \
  --out-dir artifacts/evaluation
```

Baseline comparison:

```bash
python -m nested_doc_rag.cli run-baselines \
  --config experiments/form_filling_baselines.yaml \
  --out-dir artifacts/experiments/baselines
```

Excel writeback:

```bash
python -m nested_doc_rag.cli writeback \
  --template path/to/survey_form.xlsx \
  --pred artifacts/runs/demo/predictions.jsonl \
  --out artifacts/runs/demo/filled_form.xlsx
```

## Step15AgentRunner

`Step15AgentRunner` is the recommended production-oriented runtime for gongkan
form filling. It has two layers.

Step 15 raw answer arbitration is the effect engine:

- layered Qdrant retrieval
- rerank
- full layered evidence pack
- LLM answer arbitration prompt
- answer statuses: `answered` / `partial_clue` / `not_found` / `conflict_unresolved`
- `reference_source_documents` for partial clues
- immutable raw prediction used for evaluation

Agent overlay adds production controls without mutating the raw answer:

- field state
- query trace
- evidence pack trace
- answer arbitration trace
- lightweight critic flags
- review queue
- checkpoint/resume
- optional safe Excel writeback
- writeback gating
- reference enrichment suggestions

It does not apply the strict pre-generation selected/reference gate used by
`FieldFillingAgent` v1.2. Step 15 is responsible for answer quality; the Agent
overlay is responsible for traceability, checkpointing, review routing, and
writeback safety. Evaluation uses `predictions_raw.jsonl`; production review and
writeback use `agent_overlays.jsonl`. `predictions.jsonl` remains a compatibility
copy of the raw predictions.

Recommended closed-book evaluation:

```bash
python -m nested_doc_rag.cli run-step15-agent \
  --config config/local.yaml \
  --target-namespace xixian_4 \
  --room-context "西咸4号楼 301机房" \
  --rows 4-144 \
  --retrieval-mode layered \
  --prompt-version step15_compat \
  --judge \
  --use-judge-cache \
  --out-dir artifacts/runs/step15_agent_overlay
```

Production-style run with writeback:

```bash
python -m nested_doc_rag.cli run-step15-agent \
  --config config/local.yaml \
  --target-namespace xixian_4 \
  --room-context "西咸4号楼 301机房" \
  --rows 4-144 \
  --retrieval-mode layered \
  --prompt-version step15_compat \
  --template data/forms/基地云机房信息调研表.xlsx \
  --writeback \
  --out-dir artifacts/runs/step15_agent_xixian4_writeback \
  --resume \
  --no-judge
```

`FieldFillingAgent` remains available for strict/ablation experiments.
`Step15AgentRunner` is preferred when effect quality and partial clue retention
matter.

## Lightweight Field Filling Agent

`FieldFillingAgent` is a lightweight field-level runtime for controlled form
filling. It is not a complex multi-agent system; it is a deterministic state
machine:

```text
field -> query planning -> evidence retrieval -> evidence selection -> answer generation -> validation -> one-shot repair -> human review -> writeback
```

FieldFillingAgent v1.2 adds:

- Step 15-style layered Qdrant retrieval for real backend runs.
- Selected/reference evidence channels.
- Direct evidence vs. reference clue policy.
- `partial_clue` output with `reference_chunk_ids` and `reference_source_documents`.
- Generation gating: LLM generation only runs when direct selected evidence exists.
- One-shot repair, field-level checkpoint/resume, trace, and review queue.

Evidence channels:

- Selected evidence can support `answered` and can be cited in `source_chunk_ids`.
- Reference evidence can support `partial_clue`, cannot support `answered`, and is written to `reference_chunk_ids` / `reference_source_documents`.

### Two execution modes

Offline mini mode uses the bundled mini corpus plus the deterministic generator.
It has no Qdrant, embedding, rerank, LLM, or API key dependency, so it is suitable
for tests and reproducible demos:

```bash
python -m nested_doc_rag.cli run-agent \
  --config config/local.example.yaml \
  --gold examples/mini_data/gold_fields.jsonl \
  --corpus examples/mini_data/knowledge_chunks.jsonl \
  --target-namespace xixian_4 \
  --retrieval-backend mini \
  --generation-backend deterministic \
  --out-dir artifacts/runs/demo \
  --no-writeback
```

Real backend mode uses Qdrant retrieval, an embedding service, optional rerank,
and LLM answer generation. Use `--fields` for real filling inputs; `--gold` is
kept for eval-compatible demo data. If a FieldGold-compatible file is reused,
`expected_value` is only evaluation gold and is not used for generation:

```bash
python -m nested_doc_rag.cli run-agent \
  --config config/local.yaml \
  --fields artifacts/form/form_fields.jsonl \
  --target-namespace xixian_4 \
  --room-context "西咸4号楼 301机房" \
  --retrieval-backend qdrant \
  --retrieval-plan layered \
  --generation-backend llm \
  --enable-rerank \
  --resume \
  --checkpoint-every 1 \
  --template data/forms/基地云机房信息调研表.xlsx \
  --out-dir artifacts/runs/agent_v1_2_xixian4
```

The LLM sees only selected direct evidence. `no_evidence`, `clue_only`, and
`conflict_unresolved` fields do not call the LLM and are sent to review.

Outputs:

- `predictions.jsonl`
- `trace.jsonl`
- `trace_summary.json`
- `trace.md`
- `review_items.jsonl`
- `run_summary.md`
- checkpoint/resume sidecars: `predictions.checkpoint.jsonl`, `trace.checkpoint.jsonl`, `review_items.checkpoint.jsonl`, `run_state.json`

Legacy wrappers are still available when a migrated numbered step is needed:

```bash
python apps/legacy_wrappers/step11_embedding.py --config config/local.example.yaml
python apps/legacy_wrappers/step12_form_analysis.py --config config/local.example.yaml
python apps/legacy_wrappers/step15_qdrant_eval.py --config config/local.example.yaml
```

## Remote services

The code uses configurable HTTP endpoints for:

- Qwen3 embedding: `qwen3-embedding-8b`
- Reranker
- DeepSeek-compatible chat completions

API keys are not stored in the repository. Pass them through command-line
arguments or environment variables, depending on the step.

## Examples

Run the packaged CLI from the repository root after `python -m pip install -e .`.
Use `config/local.example.yaml` as a starting point for local paths and service
endpoints.

```bash
python -m nested_doc_rag.cli show-config --config config/local.example.yaml
python -m nested_doc_rag.cli run-baselines --config experiments/form_filling_baselines.yaml
python -m nested_doc_rag.cli eval-fields \
  --gold examples/mini_data/gold_fields.jsonl \
  --pred examples/mini_data/predictions.jsonl \
  --out-dir artifacts/evaluation
```

For production runs, put private data, vector stores, and generated workbooks
under ignored local paths such as `data/` and `artifacts/`, then pass those
paths through YAML config or CLI arguments.
