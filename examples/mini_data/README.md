# Mini Data Example

This directory is reserved for a small, non-sensitive fixture dataset that can
exercise the package pipeline without using the private datacenter knowledge
files.

Planned contents:

- a tiny file manifest
- a few parsed segments
- one gongkan-style eval item
- a small Qdrant-free retrieval fixture

Current field-eval fixture:

```bash
python -m nested_doc_rag.cli eval-fields \
  --gold examples/mini_data/gold_fields.jsonl \
  --pred examples/mini_data/predictions.jsonl \
  --out-dir artifacts/evaluation
```

Private source documents and generated artifacts should stay outside Git.
