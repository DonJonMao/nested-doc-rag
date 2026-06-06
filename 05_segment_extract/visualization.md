# Step 05 规则化行级 Segment 抽取

本步骤代码：

- `extract_segments.py`
- `test_extract_segments.py`

本步骤输出：

- `../artifacts/05_segment_extract/segments.jsonl`
- `../artifacts/05_segment_extract/sheet_mappings.jsonl`
- `../artifacts/05_segment_extract/summary.json`
- `../artifacts/05_segment_extract/visualization.md`

当前步骤目标：

```text
只处理 knowledge_base Excel；
规则识别标准知识库表头；
一行能力项生成一个 excel_capability_row segment；
合并单元格类别和能力描述向下继承；
同行证明材料图片作为 proof_attachments 挂载；
图片不 OCR，不参与 embedding_text。
```

运行命令：

```bash
conda run -n datacenter python /Users/mao/projects/datacenter/05_segment_extract/extract_segments.py
conda run -n datacenter python /Users/mao/projects/datacenter/05_segment_extract/test_extract_segments.py
```
