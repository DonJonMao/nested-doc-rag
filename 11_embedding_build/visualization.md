# Step 11 嵌入构建说明

本步骤产物写入 `artifacts/11_embedding_build/`。

## 子步骤

1. `manifest`：合并 Step 05 主 Excel 行级块、Step 09 嵌入 Word 表格块、Step 10 语义审阅策略，生成 `ingestion_manifest.jsonl`。
2. `index`：只对 `default_index=true` 的记录调用 Qwen3 embedding，生成本地 `index_embeddings.f32` 和 `index_records.jsonl`。
3. `smoke`：对查询加 Qwen3 query instruction，先向量召回，再调用 reranker，生成 `retrieval_smoke.md/json`。

## 默认入库策略

- `main_excel_capability`：全部进入默认事实索引。
- `embedded_word_table`：只让 `embed`、`embed_preferred`、`embed_with_parent` 及其 image evidence 变体进入默认索引。
- `metadata_only`、`embed_as_template`、`exclude` 保留在清单中，但不进入普通事实检索。

## 运行方式

```bash
conda run -n datacenter python 11_embedding_build/embedding_pipeline.py run --limit 160 --batch-size 16
```

`--limit 0` 表示对全量默认入库记录构建索引。
