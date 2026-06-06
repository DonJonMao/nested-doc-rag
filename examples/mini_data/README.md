# Mini Data Example

This directory is reserved for a small, non-sensitive fixture dataset that can
exercise the package pipeline without using the private datacenter knowledge
files.

Current field-eval fixture:

```bash
python -m nested_doc_rag.cli eval-fields \
  --gold examples/mini_data/gold_fields.jsonl \
  --pred examples/mini_data/predictions.jsonl \
  --out-dir artifacts/evaluation
```

Current baseline fixture:

```bash
python -m nested_doc_rag.cli run-baselines \
  --config experiments/form_filling_baselines.yaml \
  --out-dir artifacts/experiments/baselines
```

`knowledge_chunks.jsonl` is a tiny Qdrant-free corpus with target namespace,
global namespace, layered evidence, and an intentionally repairable boolean
field. It is designed to test baseline framework behavior without private
datacenter documents or remote model services.

Private source documents and generated artifacts should stay outside Git.
