# Step 14 基地云机房信息调研表闭卷 RAG 评估

本步骤从 `基地云机房信息调研表.xlsx` 抽 10 行做闭卷评估。G 列 `机房信息` 作为 held-out answer，仅用于最终 judge，不进入检索 query、embedding、rerank 或答案生成 prompt。

## 样本

默认样本行：

```text
4, 5, 13, 16, 25, 26, 31, 36, 53, 117
```

目标分库：

```text
xixian_4 + global
```

评估索引包含 572 条可入库记录。

## 当前结果

报告文件：

- `artifacts/14_gongkan_rag_eval/eval_report.md`
- `artifacts/14_gongkan_rag_eval/eval_results.jsonl`
- `artifacts/14_gongkan_rag_eval/masked_eval_inputs.jsonl`
- `artifacts/14_gongkan_rag_eval/summary.json`

当前 10 行结果：

```text
acceptable/exact: 2/10
partial_or_better: 4/10
average_score: 0.32
```

## 低分原因

这次低分主要暴露的不是“答案列泄漏”问题，而是知识源和输入上下文问题：

- row 4 `机房名称`：知识库命中到 `西咸数据中心4号楼` 和地址，但未明确给出 `301机房`。
- row 13 `机房机柜数量`：`293` 在 `xixian_4 + global` 当前入库语料中没有命中。
- row 16 `机柜尺寸（U位）`：知识库有 `2200*1200*600mm` 和分楼层 U 数，但闭卷输入没有 `301机房`，无法确定应取三层 `48U`。
- row 25 `当前温湿度`：知识库有温湿度标准范围，没有 held-out 中的当前读数 `25-26.5℃`、`45.2-56.2%`。
- row 31 `变压器容量`：`2500KVA` 在当前入库语料中没有命中。
- row 36 `油机容量`：知识库有 `2000KVA`，没有 held-out 的 `2000KW*10` 台数和单位表达。
- row 53 `UPS容量`：`500KVA` 在当前入库语料中没有命中。
- row 117 `进出登记`：当前命中的是门禁/CCTV满足情况，未命中 `掌上运维`、登记表流程等具体依据。

## 结论

闭卷流程能保证 G 列不进入输入，但这张表的很多答案需要更细的待填对象上下文，例如 `4号楼-301机房`。如果生产填表时只能给 `xixian_4`，RAG 无法稳定填出机房级答案；需要外部传入目标机房/楼层/房间号，或把这些机房级台账补充进知识库。
