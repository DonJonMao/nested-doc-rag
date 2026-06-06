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

The historical `01_*` through `15_*` directories remain in place for continuity
with the existing pipeline and artifacts. New reusable code lives under
`src/nested_doc_rag`. Steps that are actively being refactored, such as
`11_embedding_build`, `12_gongkan_form_analysis`, and
`15_vector_store/evaluate_base_cloud_qdrant.py`, are now thin legacy wrappers
around package modules. Equivalent wrapper entry points also live in
`apps/legacy_wrappers`.

## Pipeline

- `01_file_registration`: register files and basic metadata.
- `02_datacenter_routing`: route files into datacenter namespaces.
- `03_format_probe`: detect file formats and embedded object candidates.
- `04a_structure_parse`: deterministic parsing for Excel/Word/PDF-like
  structures.
- `04b_embedded_object_parse`: drill into embedded Word/Excel/text objects.
- `05_segment_extract`: produce text/table segments.
- `06_segmentation_audit`: sample and inspect segment quality.
- `07_agent_need_audit`: identify cases that need model assistance.
- `08_llm_structure_hint`: use an LLM for structure hints where deterministic
  parsing is not enough.
- `09_table_candidate_resolution`: resolve table segmentation candidates.
- `10_semantic_segment_audit`: audit semantic segment quality.
- `11_embedding_build`: build ingestion manifest and embedding smoke tests.
- `12_gongkan_form_analysis`: analyze gongkan form structure.
- `13_gongkan_rag_inputs`: construct RAG input structures.
- `14_gongkan_rag_eval`: closed-book form evaluation against a lightweight
  index.
- `15_vector_store`: full Qdrant index, flat/layered rerank retrieval, and
  closed-book evaluation.

## Remote services

The code uses configurable HTTP endpoints for:

- Qwen3 embedding: `qwen3-embedding-8b`
- Reranker
- DeepSeek-compatible chat completions

API keys are not stored in the repository. Pass them through command-line
arguments or environment variables, depending on the step.

## Examples

Build the full Qdrant index:

```bash
conda run -n datacenter python 15_vector_store/full_qdrant_index.py
```

Run the 10-row closed-book evaluation with layered retrieval:

```bash
conda run -n datacenter python 15_vector_store/evaluate_base_cloud_qdrant.py \
  --retrieval-mode layered \
  --room-context '西咸4号楼 301机房' \
  --deepseek-api-key "$DEEPSEEK_API_KEY"
```

Run the full gongkan form closed-book evaluation:

```bash
conda run -n datacenter python 15_vector_store/evaluate_base_cloud_qdrant.py \
  --rows all \
  --retrieval-mode flat \
  --room-context '西咸4号楼 301机房' \
  --deepseek-api-key "$DEEPSEEK_API_KEY" \
  --out-dir artifacts/15_vector_store/base_cloud_full_form_closed_book
```
