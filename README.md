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

## Lightweight Field Filling Agent

`FieldFillingAgent` is a lightweight field-level runtime for controlled form
filling. It is not a complex multi-agent system; it is a deterministic state
machine:

```text
field -> query planning -> evidence retrieval -> evidence selection -> answer generation -> validation -> one-shot repair -> human review -> writeback
```

Offline mini-data run:

```bash
python -m nested_doc_rag.cli run-agent \
  --config config/local.example.yaml \
  --gold examples/mini_data/gold_fields.jsonl \
  --corpus examples/mini_data/knowledge_chunks.jsonl \
  --target-namespace xixian_4 \
  --out-dir artifacts/runs/demo \
  --no-writeback
```

Outputs:

- `predictions.jsonl`
- `trace.jsonl`
- `trace_summary.json`
- `trace.md`
- `review_items.jsonl`
- `run_summary.md`

The mini-data mode is fully offline. It does not require Qdrant, LLM services,
embedding/rerank endpoints, or API keys.

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
