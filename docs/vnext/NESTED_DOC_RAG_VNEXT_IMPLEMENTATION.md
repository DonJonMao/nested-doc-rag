# Nested Doc RAG vNext 技术迭代实施规格

> 用途：作为 `DonJonMao/nested-doc-rag` 下一轮技术迭代的唯一实现规格，并与文末 Codex Prompt 配合使用。  
> 目标：将当前项目从“针对固定工勘表的 Step15 RAG + 写回流水线”升级为“面向真实上传知识库与表单、证据可定位、必要时自适应补查、可安全写回的工勘文档 Agent”。  
> 非目标：不为了面试观感堆叠 Agent、框架、强化学习、知识图谱、GraphRAG 或复杂多模态模块；不通过大量启发式阈值和线性加权公式制造“复杂度”。

---

## 0. 基线与修改边界

### 0.1 基线版本

所有修改必须基于以下基线开始：

- Repository: `DonJonMao/nested-doc-rag`
- Branch: `ops/docker-worker-python-core`
- Baseline commit: `030300065e0d5041f581f573e772f7756e54abd6`
- 当前生产配置口径：`config/docker.yaml`
- 当前部署口径：`writeback-37-final`

在开始任何修改前，Codex 必须执行并记录：

```bash
git status
git branch --show-current
git rev-parse HEAD
git log -1 --oneline
```

若当前 `HEAD` 不是 `030300065e0d5041f581f573e772f7756e54abd6`，不得直接修改；先明确差异。

建议新建实现分支：

```bash
git checkout -b feat/evidence-grounded-vnext 030300065e0d5041f581f573e772f7756e54abd6
```

### 0.2 当前系统的真实主路径

当前生产主链路是：

```text
Go API / Worker
    ↓
python -m nested_doc_rag.cli run-step15-agent
    ↓
历史 form_items.jsonl 读取待填字段
    ↓
固定 layered retrieval
    ↓
Answer Arbitration LLM
    ↓
EvidenceStrength / FieldBinding / Overlay
    ↓
Excel writeback
    ↓
run_manifest / audit / review_items
```

当前结构中值得保留的部分：

1. `predictions_raw.jsonl` 与后续 overlay 分离；
2. 字段级 trace 与 artifact；
3. Qdrant + reranker 的检索基础设施；
4. Excel 写回审计；
5. Go Worker 与 Python Core 的进程边界；
6. Docker 化部署结构。

当前不应该保留为“最终设计”的部分：

1. 平台上传知识的 `source_type` 与生产检索类型不一致；
2. 上传表单仅被作为写回模板，待填字段仍来自历史 `form_items.jsonl`；
3. 固定五层全部检索，无法根据证据是否充足决定是否继续补查；
4. 证据主要停留在 chunk 级，表格字段/单元格/段落级定位不够稳定；
5. `safe` 写回对 confirmed 答案仍可覆盖非空普通单元格；
6. knowledge namespace 重建先删后写，失败可能破坏已有可用索引；
7. 若继续扩展 `equivalent_mas`，容易变成“角色命名包装流水线”，不应作为本轮重点。

---

# 1. vNext 的核心设计

本轮只引入三个核心概念：

## 1.1 Unified Evidence Record

所有入库内容，无论来自 Excel、Word、文本还是旧解析产物，都统一成同一种可检索证据记录。

核心不是统一成“字符串”，而是统一成：

```text
evidence
├── semantic content
├── evidence type
├── source location
├── structural context
└── attachment references
```

目标是解决当前：

```text
uploaded_excel_row
uploaded_docx_paragraph
...
```

与：

```text
main_excel_capability
embedded_word_table
...
```

彼此割裂的问题。

## 1.2 Addressable Evidence

答案必须能够追溯到“具体哪一份文件、哪个 sheet / table / row / cell / paragraph”。

不再把 `chunk_id` 视为最终证据坐标。

这一点借鉴 Google TableRAG 对 schema / cell / row / column 的区分，以及 Google LangExtract 对原文 span alignment 的设计思想，但不直接依赖它们的完整框架。

## 1.3 Sufficiency-Guided Retrieval

不是“第一轮检索后再加一个 Agent”，而是：

```text
query
→ primary retrieval
→ evidence sufficiency decision
→ sufficient: answer
→ insufficient: formulate missing evidence need
→ targeted supplementary retrieval
→ answer / abstain
```

只在证据不足时做第二次检索，最多一次补查。

不允许无限 ReAct 循环。

---

# 2. 总体实施顺序

必须严格按下面顺序实施，不允许为了“好看”跳过基础契约先做 Agent。

```text
Phase 0  Baseline freeze + tests
Phase 1  输入契约统一
Phase 2  结构化证据寻址
Phase 3  Sufficiency-guided targeted retrieval
Phase 4  写回安全与版本化入库
Phase 5  面试级评测、可视化与文档
```

其中：

- Phase 1、2 是必须完成项；
- Phase 3 是核心技术亮点；
- Phase 4 是工程完整性；
- Phase 5 是面试展示层；
- 本轮不做 GraphRAG、MAS 拓扑、RL、RAG reward shaping。

---

# 3. Phase 0：冻结基线与建立回归测试

## 3.1 目标

在改代码前建立“旧行为能否保持”的基线。

## 3.2 新增文件

建议新增：

```text
tests/integration/
    test_upload_to_retrieval_contract.py
    test_uploaded_form_to_fields.py
    test_evidence_address_roundtrip.py
    test_adaptive_retrieval.py
    test_safe_writeback_policy.py
```

并增加：

```text
docs/vnext/
    BASELINE.md
```

### `BASELINE.md` 至少记录

```text
baseline_commit
branch
python version
go version
production config
current test count
known writeback-37 result
known limitations
```

## 3.3 当前必须先写的 failing tests

本阶段不要先修 bug，先写出能证明现状问题的测试。

### Test A：上传知识可以被生产检索层召回

现状：

`ingestion.py` 生成：

```text
uploaded_excel_row
uploaded_docx_paragraph
uploaded_docx_table_row
uploaded_text_chunk
```

而 `config/docker.yaml` layered retrieval 只查询：

```text
main_excel_capability
embedded_word_table
embedded_raw_segment
intro_doc_paragraph
intro_doc_table_row
```

新增测试：

```python
def test_uploaded_excel_record_is_retrievable_by_production_plan(...):
    ...
```

初始测试应该失败。

### Test B：上传表单能够生成本次任务字段

新增：

```python
def test_uploaded_form_drives_form_items_instead_of_historical_artifact(...):
    ...
```

断言：

- 上传模板名不是 `基地云机房信息调研表.xlsx`；
- 不依赖历史 `artifacts/12_gongkan_form_analysis/form_items.jsonl`；
- 仍可生成 field items；
- target cell 对应真实上传表。

该测试在基线应失败。

---

# 4. Phase 1：统一知识入库契约

## 4.1 涉及模块

重点修改：

```text
src/nested_doc_rag/ingestion.py
src/nested_doc_rag/embedding/manifest.py
src/nested_doc_rag/retrieval/qdrant_retriever.py
src/nested_doc_rag/retrieval/layered.py
src/nested_doc_rag/config.py
config/docker.yaml
tests/test_ingestion_cli.py
tests/test_agent_layered_retrieval.py
```

建议新增：

```text
src/nested_doc_rag/schemas/evidence.py
src/nested_doc_rag/ingestion/normalizer.py
```

如果拆 package 会造成过大迁移，可暂时只增加：

```text
src/nested_doc_rag/evidence_record.py
```

不要为了目录“漂亮”重构整个项目。

---

## 4.2 新证据 Schema

新增 `EvidenceRecord`。

建议数据结构：

```python
@dataclass(frozen=True)
class EvidenceAddress:
    file_name: str
    relative_path: str
    sheet_name: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    cell_range: str | None = None
    paragraph_index: int | None = None
    source_anchor: str | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    chunk_id: str
    point_id: str

    namespace: str
    knowledge_base_id: str

    evidence_kind: str
    corpus_layer: str

    raw_text: str
    text_for_embedding: str

    address: EvidenceAddress

    structural_path: list[str]
    field_name: str | None
    field_value: str | None

    proof_attachment_ids: list[str]
    proof_attachments: list[dict[str, Any]]

    metadata: dict[str, Any]
```

### `evidence_kind`

只保留少量有物理意义的类型：

```text
structured_field
table_row
paragraph
document_chunk
document_intro
```

不要继续扩大为十几个 `source_type`。

### 向后兼容

Qdrant payload 仍可保留旧字段：

```text
source_type
anchor
sheet_name
row_index
...
```

但它们由新 Schema 派生。

例如：

```python
legacy_source_type = evidence_kind_to_legacy_source_type(record.evidence_kind)
```

真正检索逻辑应逐步依赖：

```text
evidence_kind
corpus_layer
namespace
```

而不是业务历史字符串。

---

## 4.3 Excel 入库修改

当前 Excel 是整行拼接：

```python
" / ".join(values)
```

修改后不应直接把每行都当“主事实”。

### 新逻辑

先识别每一行是否具备字段结构：

```text
field label | value | evidence / note
```

建议采取确定性优先的解析：

1. 读取合并单元格；
2. 保留列坐标；
3. 查找非空 cell；
4. 如果行中存在明显 label/value 关系，则生成 `structured_field`；
5. 否则生成 `table_row`；
6. 表头行不应误标为 `structured_field`。

不要使用 LLM 解析每个 Excel 行。

建议实现：

```python
def extract_excel_evidence_rows(...) -> Iterable[EvidenceRecord]:
    ...
```

### `structured_field` 必须包含

```json
{
  "field_name": "机房名称",
  "field_value": "西咸4号楼",
  "address": {
    "sheet_name": "能力清单",
    "row_index": 2,
    "cell_range": "A2:B2"
  }
}
```

Embedding 文本可构造成：

```text
文件=能力清单.xlsx
Sheet=能力清单
字段=机房名称
值=西咸4号楼
```

raw_text 保存原始行。

### 表头识别

至少支持：

- 第一行字段名；
- 合并表头；
- 空值；
- 图片单元格；
- 单元格公式。

要求测试覆盖。

---

## 4.4 Word 入库修改

Word 不再只生成：

```text
uploaded_docx_paragraph
uploaded_docx_table_row
```

转换为：

```text
paragraph
table_row
```

保留：

```text
paragraph_index
table_index
row_index
```

并保存 section / heading path。

建议新增简单 heading stack：

```python
heading_path = ["供电系统", "UPS"]
```

不要引入复杂文档树框架。

---

## 4.5 修改 layered retrieval

### 当前问题

生产计划依赖固定 `source_types`。

### 改造目标

配置改为：

```yaml
layered_plan:
  - layer_name: target_structured_fact
    namespaces: target
    corpus_layers: [fact, evidence]
    evidence_kinds: [structured_field]
    vector_top_k: 16
    rerank_top_n: 5

  - layer_name: target_table_detail
    namespaces: target
    corpus_layers: [fact]
    evidence_kinds: [table_row]
    vector_top_k: 12
    rerank_top_n: 3

  - layer_name: target_text_detail
    namespaces: target
    corpus_layers: [fact, raw_text]
    evidence_kinds: [paragraph, document_chunk]
    vector_top_k: 12
    rerank_top_n: 3
```

全局知识同理。

### `QdrantRetriever`

修改：

```python
search_by_vector(
    ...,
    evidence_kinds: list[str] | None = None,
    source_types: list[str] | None = None,  # compatibility only
)
```

优先使用 `evidence_kind`。

如果旧 collection 没有该字段，可以 fallback `source_type`。

### Acceptance

平台新上传 Excel 后：

```text
upload
→ ingestion
→ qdrant
→ production layered retrieval
```

必须能返回它的记录。

---

# 5. Phase 1B：让上传表单真正生成本次任务字段

这是本轮第二个 P0。

## 5.1 涉及模块

修改：

```text
src/nested_doc_rag/cli.py
src/nested_doc_rag/gongkan_eval.py
go-server/internal/python/command_builder.go
go-server/internal/python/types.go
go-server/internal/jobs/python_handlers.go
go-server/internal/form/materializer.go
go-server/internal/form/payload.go
```

建议新增：

```text
src/nested_doc_rag/form/template_parser.py
```

---

## 5.2 新增 CLI

新增命令：

```bash
python -m nested_doc_rag.cli parse-form-template \
  --template input.xlsx \
  --out form_items.jsonl
```

或者在 `run-step15-agent` 内自动生成。

推荐：

**独立 parser + run 时自动调用。**

原因是 parser 输出可以单独检查、缓存、测试。

---

## 5.3 FormItem 新契约

每个字段：

```json
{
  "form_item_id": "sha256(...)",
  "file_name": "new_form.xlsx",
  "sheet_name": "Sheet1",
  "row_index": 12,
  "target_cell": "F12",
  "category_path": ["供配电", "市电"],
  "question_text": "市电路数",
  "instruction_text": "填写当前实际路数",
  "answer_example": null,
  "needs_evidence": false
}
```

### parser 原则

不允许把“固定最后一列就是答案”写死在核心层。

应实现可解释规则：

```text
1. 找到标题 / 字段列；
2. 找到可填写目标列；
3. 继承分组/合并单元格作为 category_path；
4. 空问题行跳过；
5. 已有公式列不可作为 target；
6. 输出 parser diagnostics。
```

### parser diagnostics

新增：

```text
form_parse_report.json
```

内容：

```json
{
  "sheet_count": 2,
  "detected_fields": 141,
  "ambiguous_rows": [17, 23],
  "skipped_rows": [...],
  "target_column_by_sheet": {...}
}
```

如果存在无法确定的 target column，任务应该停止，不能猜。

---

## 5.4 Go → Python

`Step15RunRequest` 增加：

```go
FormItemsPath string
```

Go materializer：

```text
download template
→ invoke / request form parsing
→ obtain form_items.jsonl
→ pass --form-items
```

更简洁的实现是：

Go 只负责传入：

```text
--template
```

Python `run-step15-agent` 在未传 `--form-items` 时：

```python
form_items_path = parse_template_to_run_dir(args.template, args.out_dir)
```

推荐这个方案，减少 Go 端业务逻辑。

于是：

```python
if args.form_items:
    form_items_path = args.form_items
elif args.template:
    form_items_path = parse_form_template(...)
else:
    fallback historical path  # compatibility only
```

历史 fallback 要加 warning：

```text
LegacyFormItemsFallbackWarning
```

---

# 6. Phase 2：结构化证据寻址（Addressable Evidence）

这是本项目最适合拿去面试讲的技术点之一。

## 6.1 目标

从：

```text
答案 → chunk_id
```

升级为：

```text
答案
  → evidence_id
  → 文档
  → sheet/table/paragraph
  → row/cell
  → 原始文本
  → 可选图片附件
```

---

## 6.2 涉及模块

修改：

```text
src/nested_doc_rag/schemas/eval.py
src/nested_doc_rag/evaluation/step15_engine.py
src/nested_doc_rag/agent/step15_runner.py
src/nested_doc_rag/excel/writeback.py
src/nested_doc_rag/artifacts.py
```

新增：

```text
src/nested_doc_rag/schemas/evidence.py
src/nested_doc_rag/evidence_resolver.py
```

---

## 6.3 EvidenceRef

统一输出：

```python
@dataclass(frozen=True)
class EvidenceRef:
    chunk_id: str
    knowledge_base_id: str
    namespace: str

    file_name: str
    relative_path: str

    evidence_kind: str

    sheet_name: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    cell_range: str | None = None
    paragraph_index: int | None = None

    source_text: str | None = None

    attachment_ids: list[str] = ...
```

`FieldPrediction` 新增：

```python
evidence_refs: list[EvidenceRef]
```

原：

```python
source_chunk_ids
evidence_attachment_ids
reference_source_documents
```

暂不删除，继续派生输出兼容。

---

## 6.4 LLM 输出不直接生成任意地址

LLM 仍只允许选择检索包中的 `chunk_id` / `evidence_id`。

然后代码负责：

```python
resolve_evidence_refs(prediction.source_chunk_ids, top_hits)
```

生成真实地址。

原因：

- 防止模型伪造 cell；
- 地址来自系统 metadata；
- 更容易做 artifact validation。

---

## 6.5 prompt 调整

`build_qdrant_answer_messages()` 中 retrieved evidence 变成：

```json
{
  "evidence_id": "rag_xxx",
  "kind": "structured_field",
  "field_name": "UPS容量",
  "field_value": "500kVA",
  "location": {
    "file": "能力清单.xlsx",
    "sheet": "Sheet1",
    "cell_range": "B32:C32"
  },
  "text": "..."
}
```

系统提示改成：

```text
只能引用 evidence_id，不得生成不存在的证据地址。
```

不要让模型重复生成 file/sheet/cell。

---

## 6.6 写回 Evidence Sheet

`filled_form.xlsx` 增加可选 `Evidence` sheet：

| Field | Answer | Status | Source | Location | Evidence |
|---|---|---|---|---|---|

示例：

```text
UPS容量 | 500kVA | confirmed | 能力清单.xlsx | Sheet1!B32:C32 | UPS容量 / 500kVA
```

如果有图片：

- Evidence sheet 后部放缩略图；
- 主表 comment 只写引用位置；
- 不在主表塞大图。

---

## 6.7 验证器

`validate-artifacts` 增加：

```text
EV_REF_NOT_IN_RETRIEVAL
EV_REF_MISSING_ADDRESS
EV_CELL_RANGE_INVALID
EV_FILE_NOT_IN_KB
EV_ATTACHMENT_NOT_FOUND
```

Acceptance：

任何 `confirmed` 写回答案必须满足：

```text
至少一个 evidence_ref
且 evidence_ref 可从当前 retrieval hits 解析
且 source_text 非空
且地址可定位
```

---

# 7. Phase 3：Sufficiency-Guided Targeted Retrieval

这是本轮的核心算法迭代。

不是“新加一个 Agent”，而是改变检索决策机制。

---

## 7.1 当前问题

当前每个 field：

```text
固定五层全部检索
→ LLM 一次回答
```

存在两个问题：

1. 容易把大量无关低优先级证据扔给模型；
2. 真正缺少一个关键 slot 时，系统不会针对缺口重新查。

---

## 7.2 新流程

改为：

```text
Field Query
   ↓
Primary Retrieval
   ↓
Evidence Sufficiency Check
   ├── sufficient → Answer Arbitration
   └── insufficient
           ↓
      Missing Evidence Need
           ↓
      Targeted Retrieval
           ↓
      Merge New Evidence
           ↓
      Answer Arbitration
```

最大 retrieval round：

```text
2
```

即：

- round 0: primary
- round 1: targeted supplement

不允许第三轮。

---

## 7.3 Primary Retrieval

Primary 只取最可靠的目标域数据：

```text
target_structured_fact
target_table_detail
```

不要一开始就把 global intro 全塞进去。

建议：

```yaml
primary_layers:
  - target_structured_fact
  - target_table_detail
```

---

## 7.4 Sufficiency Object

新增：

```python
@dataclass(frozen=True)
class EvidenceSufficiency:
    sufficient: bool
    missing_facts: list[str]
    supporting_evidence_ids: list[str]
    reason: str
```

### 评估方式

本轮不要训练额外模型。

使用一次受约束 LLM structured output：

```json
{
  "sufficient": false,
  "missing_facts": [
    "当前UPS额定容量"
  ],
  "supporting_evidence_ids": ["..."],
  "reason": "现有证据只有UPS品牌，没有容量"
}
```

重要：

`sufficient` 不是置信度分数。

不要设计：

```text
score = a*coverage + b*relevance + c*...
```

---

## 7.5 Missing Evidence Query

由缺失事实构造第二轮 query。

例如原字段：

```text
UPS配置情况
```

第一轮找到：

```text
品牌=维谛
```

但要求：

```text
品牌 + 容量 + 冗余模式
```

则：

```text
missing_facts = ["UPS容量", "UPS冗余模式"]
```

构建：

```text
目标机房=xixian_4
字段=UPS配置情况
缺失事实=UPS容量；UPS冗余模式
```

只补查这些缺口。

---

## 7.6 Targeted Retrieval

第二轮可以开放：

```text
target_text_detail
global_detail
global_intro
```

但优先级必须保持：

```text
target > global
structured > raw
```

Global 仍不能无条件覆盖 target。

建议新增：

```python
def retrieve_targeted(
    missing_facts: list[str],
    ...
) -> Step15RetrievalResult:
```

不要复制一套完整 retrieval engine。

---

## 7.7 Evidence Merge

第一轮和第二轮结果按：

```text
evidence_id 去重
```

合并。

不要设计手工线性分数。

最终 prompt 中记录：

```json
{
  "retrieval_round": 0
}
```

或：

```json
{
  "retrieval_round": 1,
  "triggered_by": "UPS容量"
}
```

---

## 7.8 Trace

新增事件：

```text
primary_retrieval_completed
evidence_sufficiency_checked
targeted_retrieval_started
targeted_retrieval_completed
answer_arbitrated
```

`trace.jsonl` 中可直接画出：

```text
为什么进行了第二轮检索
```

这是面试演示重点。

---

## 7.9 失败原则

如果 targeted retrieval 后仍不足：

```text
answer_status = partial_clue / not_found
writeback_allowed = false
```

不要为了 coverage 强行回答。

---

# 8. Phase 3B：Table-Aware Retrieval，不直接引入 TableRAG

Google TableRAG 的价值在于把表理解拆成 schema / cell 等不同检索对象。

我们只吸收这个思想。

---

## 8.1 本项目适合的两级索引

对 Excel：

```text
Field Schema Index
Cell/Value Evidence Index
```

### Field Schema Index

每个字段：

```text
field_name
category_path
sheet_name
column headers
unit
```

### Value Evidence Index

每个实际字段事实：

```text
field_name + field_value + location
```

---

## 8.2 查询过程

对于：

```text
“UPS容量”
```

先查 schema：

```text
UPS容量
UPS配置
额定容量
```

然后限定 cell/value retrieval 的 field family。

从物理意义上是：

```text
先确认“问题在问哪个字段”
再检索“该字段的值”
```

而不是整张表内盲目的向量最近邻。

---

## 8.3 不要直接复制 Google TableRAG

不引入：

```text
langchain EnsembleRetriever
FAISS local db
OpenAIEmbeddings
```

现有：

```text
Qdrant + Qwen embedding + reranker
```

足够。

只需要借鉴：

```text
schema retrieval → value retrieval
```

的数据分解。

---

# 9. Phase 4：安全写回策略

## 9.1 当前问题

当前 `safe` 模式：

- formula 会保护；
- uncertain 非空时不覆盖；
- confirmed 会直接覆盖普通非空 cell。

这不符合用户对“safe”的直觉。

---

## 9.2 新策略

把 writeback policy 显式化：

```yaml
writeback:
  existing_value_policy: preserve
```

允许：

```text
preserve
overwrite_confirmed
overwrite_all
```

生产默认：

```text
preserve
```

---

## 9.3 行为

### preserve

任何非空 cell：

```text
不覆盖
→ review item
→ reason=target_non_empty
```

### overwrite_confirmed

只有 confirmed 可覆盖。

### overwrite_all

显式危险模式，仅 CLI。

---

## 9.4 审计

增加：

```text
WB_TARGET_NON_EMPTY
WB_OVERWRITE_POLICY
```

所有覆盖行为记录：

```text
old_value
new_value
policy
evidence_refs
```

---

# 10. Phase 4B：知识库版本化更新

## 10.1 当前问题

当前 `upsert_records()`：

```text
delete namespace
→ embedding
→ upsert
```

中途失败会破坏已有 namespace。

---

## 10.2 新模型

Qdrant payload 新增：

```text
index_version
is_active
```

更推荐只用：

```text
index_version
```

活跃版本放 PostgreSQL / KB metadata，而不是每个 point 改 `is_active`。

---

## 10.3 更新流程

```text
build new version v_next
→ embed/upsert all records
→ validate count + smoke retrieval
→ atomically update KB active_index_version
→ later garbage-collect old version
```

检索 Filter：

```text
namespace
AND index_version = active_version
```

---

## 10.4 不要使用 collection-per-upload

不建议每次新建 collection。

原因：

- collection 管理会膨胀；
- schema 相同；
- namespace + index_version 已能隔离。

---

# 11. Phase 5：面试级 Evaluation

不要只展示 UI。

需要让面试官看到：

```text
问题
→ 方法改变
→ 可测量收益
```

---

## 11.1 固定测试集

至少保留三类：

### A. 原 141 字段

用于兼容旧项目。

### B. Challenge Set

人为构造 30–50 个难例：

```text
同值不同字段
同字段不同房间
现状 vs 规划
数字子串干扰
global 与 target 冲突
需要从 table detail 补证
需要二次 targeted retrieval
```

### C. Fresh Upload E2E

至少三份新的：

```text
知识库 + 表单
```

不能使用旧 `form_items.jsonl`。

---

## 11.2 必须报告的指标

### Retrieval

```text
Evidence Recall@K
Exact Field Evidence Recall@K
Wrong-Field Retrieval Rate
Target-vs-Global Confusion Rate
```

### Answer

```text
Answer Accuracy
Abstention Precision
Unsupported Answer Rate
```

### Writeback

```text
Writeback Precision
Writeback Coverage
Unsafe Overwrite Count
```

### Adaptive Retrieval

```text
Second-Round Trigger Rate
Second-Round Evidence Gain
Answer Gain after Targeted Retrieval
Average Retrieval Calls / Field
```

不要只给最终 Accuracy。

---

## 11.3 Ablation

最少四组：

```text
A0 current writeback-37 baseline
A1 + unified evidence schema
A2 + addressable table evidence
A3 + sufficiency-guided targeted retrieval
```

若继续做：

```text
A4 + table schema→value retrieval
```

不要同时更改 embedding model、chat model 和 retrieval 机制。

---

# 12. 面试展示页面

现有前端不需要重做。

只新增一个“证据追踪面板”。

## 12.1 展示

每个填写字段显示：

```text
Answer
Status
Evidence
Retrieval path
Writeback decision
```

例如：

```text
市电路数: 2路

Evidence:
能力清单.xlsx
Sheet: 301机房
Cells: B21:C21
Text: 市电路数 / 2路

Retrieval:
Round 0 → insufficient
Missing: 是否来自不同变电站
Round 1 → found D21:E21

Writeback:
confirmed
```

---

# 13. 不做事项

Codex 在实现过程中不得主动引入：

```text
LangChain 全量重构
LlamaIndex
新的向量数据库
Neo4j
GraphRAG
强化学习
多 Agent 投票
Planner/Critic/Executor 三角色包装
DAG 工作流框架
复杂 reward
三项以上线性加权 retrieval score
```

除非此文档明确更新。

---

# 14. 预期最终代码结构

在避免过度重构的前提下，建议最终增加：

```text
src/nested_doc_rag/
├── evidence_record.py
├── evidence_resolver.py
├── form/
│   └── template_parser.py
├── retrieval/
│   ├── layered.py
│   ├── qdrant_retriever.py
│   ├── sufficiency.py
│   └── targeted.py
```

如果已有 package 可以承载对应实现，应优先复用，不要为了匹配此目录树搬文件。

---

# 15. 具体修改清单

## P0 — 必须

### M1 Unified Evidence Contract

修改：

```text
ingestion.py
qdrant_retriever.py
layered.py
config/docker.yaml
```

新增：

```text
evidence_record.py
```

完成定义：

```text
平台上传数据可被生产 retrieval 召回
```

---

### M2 Dynamic Form Parsing

新增：

```text
form/template_parser.py
```

修改：

```text
cli.py
gongkan_eval.py
Go command builder / payload
```

完成定义：

```text
新上传表单无需历史 form_items.jsonl 即可执行
```

---

## P1 — 核心技术亮点

### M3 Evidence Addressing

修改：

```text
FieldPrediction
step15_engine.py
step15_runner.py
writeback.py
artifact validation
```

完成定义：

```text
confirmed answer 必须能定位到 file/sheet/row/cell/paragraph
```

---

### M4 Sufficiency-Guided Retrieval

新增：

```text
retrieval/sufficiency.py
retrieval/targeted.py
```

修改：

```text
step15_runner.py
step15_engine.py
config
trace
```

完成定义：

```text
只有证据不足时触发第二轮检索
```

---

## P2 — 进一步优化

### M5 Table Schema→Value Retrieval

完成定义：

```text
降低同表错字段召回
```

---

### M6 Safe Writeback

完成定义：

```text
默认不覆盖已有人工值
```

---

### M7 Versioned KB Ingestion

完成定义：

```text
新索引失败不会破坏当前可用版本
```

---

# 16. 每阶段提交建议

建议保持 Git history 可讲。

```text
commit 1:
test: freeze vnext baseline and reproduce upload contract gaps

commit 2:
refactor: unify uploaded knowledge into evidence records

commit 3:
feat: parse uploaded form templates into runtime fields

commit 4:
feat: attach addressable evidence refs to predictions

commit 5:
feat: add sufficiency-guided targeted retrieval

commit 6:
feat: add schema-first table retrieval

commit 7:
fix: preserve existing values in safe writeback mode

commit 8:
feat: version knowledge-base index activation

commit 9:
test: add fresh-upload e2e and challenge evaluation
```

不要一次大 commit。

---

# 17. Definition of Done

vNext 只有同时满足以下条件才能宣称完成。

## Input

- [ ] 新上传知识可以被真实生产 retrieval 查到；
- [ ] 新上传表单可以自己生成 FormItem；
- [ ] 不依赖旧 `form_items.jsonl`。

## Evidence

- [ ] confirmed answer 至少 1 条 EvidenceRef；
- [ ] EvidenceRef 可定位至源文档；
- [ ] 表格证据包含 row/cell；
- [ ] Word 至少包含 paragraph/table row 地址。

## Retrieval

- [ ] 第一轮只优先查 target structured evidence；
- [ ] 证据充足不会触发第二轮；
- [ ] 证据不足最多补查一轮；
- [ ] trace 明确记录补查原因。

## Writeback

- [ ] safe 默认不覆盖非空人工值；
- [ ] formula 继续保护；
- [ ] writeback audit 有 old/new value；
- [ ] 每条 confirmed 写回可追证据。

## Reliability

- [ ] KB 更新失败不删除当前 active version；
- [ ] clean Docker volume 可以走通 upload→fill→download。

## Evaluation

- [ ] old 141-field baseline 可复现；
- [ ] fresh-upload E2E ≥ 3；
- [ ] challenge set ≥ 30；
- [ ] 有 A0–A3 ablation；
- [ ] 有 unsupported answer / writeback precision 指标。

---

# 18. 面试中如何描述这轮迭代

不说：

> 我接入了 Google TableRAG、LangExtract 和阿里 AgentScope。

应该说：

> 我原来的版本在固定工勘数据上能工作，但做平台化后我发现两个根本问题：上传知识的索引契约和检索契约不一致，上传表单也没有真正驱动字段解析。第二版我先统一了 evidence schema 和动态表单解析，然后把 chunk 级 RAG 改成了可定位到具体表格字段/原文位置的 evidence-grounded retrieval。对于第一轮证据不足的字段，系统不会直接让模型猜，而是显式识别缺失事实，再只针对缺口进行一次补充检索。最后把写回和索引更新改成可审计、可回滚的生产语义。

如果被问“大厂工作有什么借鉴”：

> Google TableRAG 给我的启发是表格检索不应该把整行文本当普通 chunk，而应该区分 schema 和 value；LangExtract 的启发是生成结果要能回到原始 source span，而不是只保存一个 chunk id；AgentScope 更适合作为可观测执行框架参考，但我没有为了多智能体而重构项目，因为这个任务的瓶颈主要是 evidence contract 和检索决策，不是 agent 数量。

---

# 19. 外部参考

仅作为设计参考，不作为强依赖。

## Google TableRAG

Repository:

https://github.com/google-research/google-research/tree/master/table_rag

主要借鉴：

```text
schema retrieval
cell retrieval
row / column aware representation
```

不直接复制其 LangChain / FAISS 实现。

## Google LangExtract

Repository:

https://github.com/google/langextract

主要借鉴：

```text
source alignment
structured extraction
generation result → source span
```

不把它作为整个 ingestion 的替代品。

## Alibaba AgentScope

Repository:

https://github.com/agentscope-ai/agentscope

主要借鉴：

```text
traceable tool execution
agent runtime observability
```

本轮不强制引入 AgentScope 2.x。

---

# 20. Codex 执行 Prompt

下面 Prompt 与本 MD 一起交给 Codex。

将本文件放在仓库，例如：

```text
docs/vnext/NESTED_DOC_RAG_VNEXT_IMPLEMENTATION.md
```

然后把以下 Prompt 交给 Codex。

---

## Codex Prompt

```text
You are implementing the vNext iteration of the repository:

DonJonMao/nested-doc-rag

The authoritative implementation specification is:

docs/vnext/NESTED_DOC_RAG_VNEXT_IMPLEMENTATION.md

Read that document completely before changing code.

BASELINE
========

The implementation MUST start from:

branch:
ops/docker-worker-python-core

commit:
030300065e0d5041f581f573e772f7756e54abd6

Before doing any work, verify:

git status
git branch --show-current
git rev-parse HEAD
git log -1 --oneline

If HEAD differs from the baseline, inspect and report the difference before implementation.

PRIMARY GOAL
============

Upgrade the project from a fixed-data Step15 form-filling RAG pipeline into a real upload-driven, evidence-grounded form-filling system with:

1. one unified evidence contract for newly uploaded and legacy knowledge;
2. runtime form-field parsing from the uploaded Excel template;
3. addressable evidence references down to file/sheet/row/cell/paragraph;
4. sufficiency-guided retrieval with at most one targeted supplementary retrieval round;
5. safer Excel writeback;
6. version-safe knowledge-base ingestion;
7. strong regression and end-to-end tests.

IMPORTANT DESIGN RULES
======================

Do NOT solve problems by adding unrelated modules.

Do NOT introduce architecture for appearance.

Specifically, do not add:

- GraphRAG;
- Neo4j;
- reinforcement learning;
- multi-agent voting;
- Planner/Critic/Executor role wrappers;
- LangChain-wide refactor;
- LlamaIndex;
- a new vector database;
- arbitrary weighted reward/retrieval formulas;
- large sets of new hyperparameters.

Do not replace Qdrant, the current embedding endpoint, reranker, Go worker, or Python core architecture unless the specification explicitly requires it.

The key principle is:

fix contracts and operators, not symptoms.

IMPLEMENTATION ORDER
====================

Implement strictly in this order.

PHASE 0
-------
Freeze current behavior and add failing regression tests that reproduce:

A. uploaded ingestion records are not retrievable by the production layered retrieval plan;

B. an uploaded form template does not currently create its own runtime form-items and still depends on historical form_items.jsonl.

Do not fix those failures before the tests exist.

PHASE 1
-------
Implement Unified Evidence Record.

Requirements:

- add a canonical evidence schema;
- normalize uploaded Excel/Word/text records into it;
- preserve backward-compatible payload fields when needed;
- production retrieval should primarily filter by evidence_kind rather than historical source_type;
- uploaded knowledge must be retrievable by the same production plan;
- do not blindly rename every uploaded Excel row to main_excel_capability;
- distinguish structured_field from generic table_row.

Add or update tests.

PHASE 1B
---------
Implement runtime form parsing.

Requirements:

- an uploaded Excel template must produce the field list used in the current run;
- add a deterministic parser with diagnostics;
- avoid hard-coding the file name 基地云机房信息调研表.xlsx in the production path;
- avoid hard-coding “last column = target” in the core abstraction;
- retain the historical form_items path only as a compatibility fallback with an explicit warning;
- keep Go business logic thin; prefer Python parsing when practical.

Add tests with:
- renamed form template;
- reordered rows;
- different target column;
- merged category cells.

PHASE 2
-------
Implement Addressable Evidence.

Requirements:

- introduce EvidenceRef;
- LLMs may select an evidence ID but must not invent document locations;
- resolve file/sheet/table/row/cell/paragraph from system metadata after generation;
- add evidence_refs to FieldPrediction while retaining compatibility fields;
- confirmed writebacks must have resolvable evidence;
- update artifact validation;
- update Excel evidence comments / Evidence sheet.

PHASE 3
-------
Implement sufficiency-guided retrieval.

Requirements:

- round 0: primary retrieval from target structured evidence;
- structured sufficiency decision;
- if sufficient, answer immediately;
- if insufficient, output missing_facts;
- build one targeted supplementary query from missing_facts;
- run at most one second retrieval round;
- merge evidence by evidence_id;
- then answer or abstain;
- no open-ended ReAct loop;
- no hand-crafted multi-term weighted score;
- log why the second retrieval was triggered.

The sufficiency output is a semantic decision object, not a confidence scalar.

PHASE 3B
---------
If the earlier phases are stable, add schema-first table retrieval.

Concept:

query
→ retrieve likely field/schema
→ retrieve values/evidence constrained by that field family

Reuse Qdrant.

Do not import Google TableRAG directly.

PHASE 4
-------
Make writeback semantics explicit.

Production-safe default must preserve non-empty human values.

Support explicit policies such as:

preserve
overwrite_confirmed
overwrite_all

Audit:
- old value;
- new value;
- policy;
- evidence refs.

PHASE 4B
---------
Make knowledge ingestion version-safe.

Do not delete the active namespace before the new version is successfully built.

Implement:

build new index_version
→ validate
→ activate version
→ preserve previous version until activation succeeds

Prefer namespace + index_version in one collection rather than collection-per-upload.

TESTING REQUIREMENTS
====================

After each phase:

1. run the directly affected tests;
2. run the full Python test suite when reasonable;
3. run Go tests for touched Go modules;
4. report failures before proceeding.

At the final stage include integration tests for:

clean storage
→ upload KB
→ ingest
→ upload form
→ parse fields
→ retrieve
→ answer
→ writeback
→ validate artifacts

Evaluation must include:

- the existing 141-field workload if assets are available;
- at least one fresh-upload integration fixture in tests;
- challenge cases for wrong-field, wrong-scope, current-vs-planned, number substring, target-vs-global conflict, and second-round retrieval.

CODE QUALITY
============

Keep changes local.

Do not perform broad unrelated refactors.

Prefer dataclasses and typed structures already used by the codebase.

Keep old artifact fields until migration is proven.

Any compatibility fallback must:
- be explicit;
- emit a trace/warning;
- have a test.

Configuration additions must be minimal and semantically necessary.

Do not create multiple tunable weights to combine retrieval objectives.

GIT / WORKING STYLE
===================

Work incrementally.

Before each phase:
- inspect the relevant current implementation;
- summarize the current behavior;
- identify files to modify.

After each phase:
- summarize changed files;
- explain why the implementation matches the spec;
- list tests and results;
- state remaining risks.

Do not claim success unless tests demonstrate it.

Suggested commits:

1. test: freeze vnext baseline and reproduce upload contract gaps
2. refactor: unify uploaded knowledge into evidence records
3. feat: parse uploaded form templates into runtime fields
4. feat: attach addressable evidence refs to predictions
5. feat: add sufficiency-guided targeted retrieval
6. feat: add schema-first table retrieval
7. fix: preserve existing values in safe writeback mode
8. feat: version knowledge-base index activation
9. test: add fresh-upload e2e and challenge evaluation

You may adjust commit boundaries when necessary, but preserve the same dependency order.

FINAL DELIVERABLE
=================

When implementation is complete, provide:

1. exact base commit and final commit;
2. changed file list grouped by phase;
3. architecture/data-flow summary;
4. tests executed and results;
5. before/after behavior for the two original upload contract bugs;
6. before/after retrieval flow;
7. writeback safety behavior;
8. migration/backward compatibility notes;
9. unresolved limitations;
10. commands needed to reproduce a fresh-upload end-to-end run.

Most importantly:

Do not optimize the project for visual complexity.
Optimize it for a clear technical story:

input contract
→ structured evidence
→ evidence sufficiency
→ targeted acquisition
→ verifiable answer
→ safe writeback.
```

---

# 21. 最终一句话目标

本次 vNext 的工程目标不是：

> “把 RAG 项目做得更复杂。”

而是：

> **让系统在任意新上传的工勘知识与表单上，能够明确知道“问题是什么、证据在哪里、证据够不够、还缺什么、为什么填写，以及填写是否安全”。**

这才是整个项目最值得作为互联网大厂面试项目展示的技术主线。
