# 工勘填报项目：大厂研究调研与面试迭代路线

调研日期：2026-10-01。基于工作区和当天可访问的官方仓库、发布页、模型卡、论文及厂商博客。本文保留调研开始时的代码快照与实施建议；随后已完成上传检索契约、受约束动作和真实证据工作台的首轮迭代，详见[实施与验收记录](2026-10-01-implementation.md)。真实模型的业务效果提升尚未实测。

供阅读与分享的[中文 PDF 建议报告](../../output/pdf/datacenter-research-upgrade-proposals-20261001.pdf)已区分当前能力与八项后续建议，包含验收条件、正式界面截图和可点击官方来源。Google release feed 的 `updated` 日期不等于精确首次发布日期；论文首稿与修订分别标注。

## 1. 推荐结论

建议把项目打造成**可定位证据约束的工勘填报 Agent 系统**，优先完成四个相互支撑的增量：

1. 修复真实上传、检索、表单字段分析的链路，建立版本化领域测试集。
2. 借鉴 Google LangExtract，把引用从 chunk ID 升级为代码校验的原文区间，并用证据工作台展示。
3. 深化现有 AgentScope 2.0 和 MAS，让检索动作真正执行来源/层级限制，增加调用预算、停止原因和真实成本观测。
4. 借鉴 Google Sufficient Context / FACTS 与阿里 EvalScope，做跨机房盲测、消融和质量—成本对比。

先保持 Python Core、Go/Asynq、Qdrant、稳定产物契约。Docling 是结构解析候选，PaddleOCR 是按需扫描件/图片解析候选；Qwen3-VL 是后续独立的图片检索实验。现在不优先迁移 Google ADK，不给同进程角色加 A2A，也不全面引入 GraphRAG 或 Agent RL。

面试中有价值的是：为什么选择这些边界、怎样处理错机房/错粒度/错状态、怎样证明结果、怎样控制长任务和模型成本。开源库数量不能替代这些证据。

## 2. 调研开始时的代码事实与缺口

本节用于说明选型依据。上传来源过滤、动作约束和原文定位已经在首轮迭代中修复或补充；当前状态以[实施记录](2026-10-01-implementation.md)为准。

| 主题 | 当前真实实现 | 迭代需要解决的事 |
| --- | --- | --- |
| 产品链路 | Vue → Go API → Redis/Asynq → Worker 内 Python CLI；结果以 manifest 归档 | 保持所有者权限、取消、恢复、SSE 与下载校验 |
| 答案与写回 | 原始预测与 Overlay 分离；证据强度、字段绑定、组合槽检查可配置 | 默认与 Docker profile 启用项不同，实验必须记录有效配置 |
| 多轮 MAS | 基础链路实时运行，允许写回的字段保留；其余进入四类证据诊断工作流 | 不能把已有三轮重检索作为本轮新增能力 |
| 框架集成 | 已依赖 `agentscope>=2,<3`；bridge 用 passthrough model 触发生命周期，再执行本地 callback | 现状属于受控角色编排，不应描述成多个模型自主协商 |
| 检索动作 | action 有 `target_layer/source_type_preference`；`run_action()` 只传 query 文本 | 让动作约束进入真实 Qdrant filter / layer plan，禁止语义漂移 |
| 知识上传 | XLSX 逐行、DOCX 段落/表格、文本分块，记录类型为 `uploaded_*` | 默认和 Docker 分层来源过滤排除这些类型，先统一契约 |
| 文档结构 | 部分 parsing/segmentation 文件仍是待迁移占位；历史预处理数据由 seed 提供 | 不能宣称当前上传支持完整递归 OLE / 语义切分 |
| 表单分析 | 主要消费历史 Step12 `form_items` 和结构文件 | 新模板的字段发现需从上传工作簿生成，不能只更换写回模板 |
| 引用 | chunk IDs、anchor、参考文本、图片 registry 已存在 | 缺原文 quote/字符区间/版本哈希，尚不能展示可验证逐字出处 |
| 评估 | 字段精确/语义匹配、status、拒答、review、evidence recall 等已有 | `evidence_supported` 只检查 answered 且 source IDs 非空，不代表语义支撑 |

关键代码位置：

- [上传入库](../../src/nested_doc_rag/ingestion.py)、[默认分层配置](../../config/default.yaml)、[Docker profile](../../config/docker.yaml)。
- [角色和动作执行](../../src/nested_doc_rag/agent/mas/roles.py)、[黑板与状态路由](../../src/nested_doc_rag/agent/mas/controller.py)、[AgentScope bridge](../../src/nested_doc_rag/agent/mas/agentscope_bridge.py)。
- [Step15 主入口](../../src/nested_doc_rag/agent/step15_runner.py)、[字段指标](../../src/nested_doc_rag/evaluation/field_metrics.py)、[表单分析](../../src/nested_doc_rag/form/analyze.py)。
- [Go Worker/Python 进度](../../go-server/internal/jobs/python_handlers.go)、[结果与下载](../../go-server/internal/form/result.go)、[前端详情](../../web/src/views/FillRunDetailView.vue)。

## 3. 官方研究和仓库筛选

这里的“近期”包括 2026 年维护/发布的工具和仍适用的 2024–2025 年方法论文。日期区分论文首稿、修订、正式 release 和 feed 更新时间。没有穷尽各厂商所有研究，也不把厂商榜单成绩当作本项目效果。

| 来源 | 已核实版本或研究日期 | 内容与接入判断 | 采用优先级 |
| --- | --- | --- | --- |
| Google [LangExtract](https://github.com/google/langextract) | [v1.7.0](https://github.com/google/langextract/releases/tag/v1.7.0)，2026-09-13；Apache-2.0 | 原文 span、结构化抽取、证据高亮。优先借鉴严格 quote 校验；库作为可选 adapter | 高 |
| Google Research [Sufficient Context](https://arxiv.org/abs/2411.06037) | 首稿 2024-11-09；v3 2025-04-23；[官方解读](https://research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/) 2025-05-14 | 拆分上下文不充分与生成错误；选择性回答、拒答和 risk-coverage 评测 | 高，借鉴方法 |
| Google DeepMind [FACTS Benchmark Suite](https://deepmind.google/blog/facts-benchmark-suite-systematically-evaluating-the-factuality-of-large-language-models/) | 2025-12-09；[官方技术报告](https://storage.googleapis.com/deepmind-media/FACTS/FACTS_benchmark_suite_paper.pdf) | 借鉴任务完成与事实支撑分开评估、公开集与私有盲测；不照搬通用榜单 | 高，借鉴方法 |
| Google [ADK](https://github.com/google/adk-python) | [v2.10.0](https://github.com/google/adk-python/releases/tag/v2.10.0)，GitHub 发布 2026-09-25；Apache-2.0 | 借鉴 token、模型/工具调用数、耗时和轨迹评测；整个运行时与现有框架重叠 | 中，不立即迁移 |
| Google 发起、现独立项目 [A2A](https://github.com/a2aproject/A2A) | [v1.0.1](https://github.com/a2aproject/A2A/releases/tag/v1.0.1)，2026-05-28；Apache-2.0 | 独立 OCR/检索服务确实需要 Agent 互操作时再用 AgentCard、Task/Artifact | 低，条件采用 |
| 阿里 [AgentScope](https://github.com/agentscope-ai/agentscope) | [v2.0.9](https://github.com/agentscope-ai/agentscope/releases/tag/v2.0.9)，2026-09-28；Apache-2.0 | 深化已有依赖的事件/middleware、pipeline、受限工具；SOP 为实验性功能 | 高 |
| 阿里 [AgentScope Runtime](https://github.com/agentscope-ai/agentscope-runtime) | PyPI 1.1.6.post2，2026-06-04；Apache-2.0 | 当前 README 宣告能力合入 AgentScope 2.0，并进入只读/归档流程 | 不新增独立依赖 |
| 阿里 [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch) | 模型发布 2025-09-17；[报告](https://arxiv.org/abs/2510.24701) 2025-10-28；代码/官方模型卡 Apache-2.0 | 借鉴证据驱动迭代和停止策略，受控测试 replanner/model；整套网页研究不适配私有工勘字段 | 中，借鉴/旁路实验 |
| 通义 [Qwen3 Embedding/Reranker](https://github.com/QwenLM/Qwen3-Embedding) | [论文](https://arxiv.org/abs/2506.05176) 2025-06-05；官方模型卡 Apache-2.0 | 当前已用 Qwen3-Embedding-8B，做领域 instruction 与 rerank 候选预算消融 | 高，深化已有模型 |
| 通义 [Qwen3-VL Embedding/Reranker](https://github.com/QwenLM/Qwen3-VL-Embedding) | [论文](https://arxiv.org/abs/2601.04720) 2026-01-08；官方模型卡 Apache-2.0 | 图片、表格截图、混合输入检索；独立 collection/adapter，图像子集单独评测 | 中，第二阶段 |
| 阿里 ModelScope [EvalScope](https://github.com/modelscope/evalscope) | PyPI 1.12.0，2026-09-16；Apache-2.0 | 离线自定义字段任务、Agent trace、embedding/rerank 评测与压测 | 高，独立实验环境 |
| IBM Research 发起、现 LF AI & Data [Docling](https://github.com/docling-project/docling) | [v2.131.0](https://github.com/docling-project/docling/releases/tag/v2.131.0)，2026-09-29；MIT | Office/PDF 结构、表格、父子关系、provenance、HybridChunker | 高，受控解析 adapter |
| 百度 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | [v3.7.0](https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0)，2026-06-11；Apache-2.0 | PP-StructureV3 补扫描件/图片表格、页码与精细坐标；原生 Office 不默认 OCR | 中，按需 fallback |
| Microsoft [GraphRAG](https://github.com/microsoft/graphrag) | [v3.2.0](https://github.com/microsoft/graphrag/releases/tag/v3.2.0)，2026-09-24；MIT | 当前 README 标 maintenance mode；community reports/global search 偏全库主题聚合 | 低，关系问题再实验 |
| Microsoft [AutoGen](https://github.com/microsoft/autogen) | python-v0.7.5；feed 更新 2025-09-30；代码 MIT | 当前 README 标 maintenance mode，推荐新项目 Agent Framework；不解决本项目解析和引用缺口 | 不迁移现有编排 |
| Microsoft [Agent Lightning](https://github.com/microsoft/agent-lightning) | [v1.0.2](https://github.com/microsoft/agent-lightning/releases/tag/v1.0.2) feed 更新 2026-09-29；[v1.0 报告](https://arxiv.org/abs/2608.17528) 2026-08；MIT | 真实 harness 上的 Agent RL；需要训练数据、可信 reward 和 GPU，当前不作为第一迭代 | 低，研究延伸 |
| NVIDIA [NeMo Retriever](https://github.com/NVIDIA/NeMo-Retriever) | 26.8.2；feed 更新 2026-09-30；代码 Apache-2.0 | GPU/NIM 文档大批量解析，当前基础设施与规模下偏重；未来仅接抽取阶段 | 低，规模化再评估 |
| Databricks 发起的 [MLflow](https://github.com/mlflow/mlflow) | [v3.16.1](https://github.com/mlflow/mlflow/releases/tag/v3.16.1) feed 更新 2026-09-17；Apache-2.0 | 可选 trace/实验存储和评测平台；不必与 AgentScope/EvalScope 同时引入所有依赖 | 中，按团队需求选一套 |

许可边界：模型权重许可与代码、数据集许可分别核查。Qwen3 文本模型卡已核实 Apache-2.0，仓库根目录 LICENSE 请求为 404，不能据此声明整仓所有资产许可已确认。Sufficient Context 作者仓库不在 Google 组织下且未发现 LICENSE；FACTS 数据与报告也不是业务 SDK。本文只引用方法，没有复制未明确许可的代码/数据。

## 4. 最值得实施的技术规格

### 4.1 结构与来源契约：先让上传的真实资料可用

不要将任意上传行直接改名为强证据 `main_excel_capability`。建议把“文件提取方式”和“业务证据类型”分开：

- `parser_type`：xlsx_row / docx_paragraph / docx_table / ocr_table。
- `source_type`：经过结构识别后的能力表、下钻明细、介绍、附件；未知上传来源进入独立低优先级层。
- `namespace/document_id/index_version/source_anchor`：一直保留，不允许 fallback 放松机房或权限隔离。
- `raw_source_text`：原文；`raw_text`：可读检索证据；`text_for_embedding`：可加入标题、表头的向量文本。三个文本空间不能混用位移。

解析适配器输出当前 JSONL/manifest 契约。Docling 的 [DoclingDocument](https://docling-project.github.io/docling/concepts/docling_document/) 和 [HybridChunker](https://docling-project.github.io/docling/concepts/chunking/) 可用于表格、标题、父子关系和 token 长度控制。其 Excel backend 的位置使用从 0 开始的单元格坐标，必须显式转换到 A1，并用合并单元格/宽表 fixture 验证。支持 XLSX/DOCX 不等于自动支持递归 Office OLE，嵌入对象提取仍需自有适配器。

扫描页、DISPIMG 和图片附件按需走 [PP-StructureV3](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html)，保存页码、bbox、OCR 分数和原图片 ID；OCR 数字与单位不确定时只作为线索。原生 Office 优先读取结构，不先转图片再 OCR。

上传索引现状是先删除整个 namespace 再逐批 upsert。应改为新版本构建、验证完成后切换 active version，失败保留旧版；删除旧版单独清理。先支持版本化 payload/filter，再决定是否迁移 collection。任何索引版本切换都要与 Go current_index_version 一致。

验收：每一种上传类型都有“入库→真实过滤条件召回”的集成用例；未知类型不自动升级证据强度；构建失败不破坏旧版；相同表头、不同机房/粒度的行不串用；新上传表单生成自己的字段位置，不依赖固定 4–144 行历史表单。

### 4.2 原文证明与证据工作台

借鉴 LangExtract 的 source grounding，先实现一个不依赖新 LLM 的验证模块。模型给出的 quote 只是候选，代码负责定位、验证和标记失败。

建议新增旁路产物 `evidence_provenance.jsonl`，避免第一步改变冻结 raw prediction schema：

```json
{
  "field_id": "field_25",
  "claim_id": "power_supply_origin",
  "chunk_id": "chunk_42",
  "document_id": "doc_example",
  "index_version": "version_example",
  "source_anchor": "能力清单!H42",
  "source_text_hash": "sha256:...",
  "text_space": "raw_source_text",
  "quote": "2路进线，来自同一变电站",
  "start": 14,
  "end": 27,
  "match_status": "exact",
  "scope_checked": true,
  "writeback_allowed": true
}
```

上例只示范字段结构，start/end 与 hash 是占位，不能当作真实运行记录。

规则：

1. 使用确定的文本空间，区间采用 Unicode code point 的 `[start,end)`，由 Python 计算；JS 渲染应使用 code point 数组或显式转换 UTF-16 位移，测试 emoji/非 BMP 文本。
2. 校验 `source[start:end] == quote`、chunk/version/hash 有效；找不到为 `unmatched`，重复且无法消歧为 `ambiguous`。
3. 缺失区间、数值模糊匹配、引用不存在不能晋升为强证据。LangExtract 默认 fuzzy alignment，因此不得直接以 span 非空认定安全。
4. 原文定位仅证明“这段字确实存在”；字段、机房范围、现状/规划、单位、组合槽覆盖仍由原有门控判断。
5. HTML 高亮 escape 原文。完整来源存在权限范围内的归档对象；内部 trace 默认保留元数据和短决策理由，不把完整 prompt/evidence 写进集中日志。

UI 推荐：左侧字段列表，右侧原始答案/写回判定，下面原文高亮与来源位置；可切换“推理轨迹”，显示每轮动作、新增证据、短停止理由。冲突、粒度不足、无答案也能点开看原因。展示的是决策审计，不是模型私有思维链。最初原型为 [evidence-workbench.html](../../artifacts/design/evidence-workbench.html)，全部为合成演示数据。正式 Vue 字段证据工作台现已接入真实结果 API，运行事件沿用已有时间线；专门的逐轮动作视图仍是后续建议。

### 4.3 真正受约束的检索动作与预算

调研开始时的 `run_action()` 只改查询字符串；首轮已将层级、来源和 namespace 约束落实到实际检索。下面保留动作设计依据，预算、成本与专门的逐轮 UI 仍待实施：

| 动作 | 检索器应实际执行的约束 | 必须保持的边界 |
| --- | --- | --- |
| slot_targeted | 查询未覆盖必需槽；可带设备/单位提示 | 不用已有答案或 gold 构造查询 |
| layer_expansion | 明确允许扩展的低优先级层、每层 top_k | 保持 target/global namespace 隔离和层优先级 |
| source_specific | 真实 source_type filter；限制目标文件/表时验证权限 | 不采用模型生成的任意文件路径 |
| contrastive | 用候选主张寻找反证，保存支持/反对 chunk 集 | 不把“未找到反证”当作证明为真 |
| disambiguation | 比较相同实体、属性、粒度、时间的候选 | 无可靠版本证据时转复核 |

保留共享黑板、只读状态快照、controller 集中合并。并行建议可参考 AgentScope TeamPipeline，但 worker 不能同时写可变黑板；最终写回依旧确定性门控。

预算同时限制 rounds、retrieval actions、LLM calls、tokens 和 wall time。运行时记录每次 action 新增的不同 chunk 数、槽覆盖变化、答案变化；是否新增正确字段必须离线对照 gold 判断，在线不能自行认定答案变得正确。重复召回且没有新信息时停止。预算耗尽输出可解释 review reason，不能默默视为成功。多个动作的取舍可以先用明确规则，再做成本收益策略实验，不必先上 RL。

模型网关和 trace 记录真实 prompt/completion token、cache/reasoning token 的 provider 定义、模型/工具调用数、耗时和重试。缺失 usage 是 `null/unknown`，不是 0；避免把包含在 completion 内的 reasoning token 再加一次。价格与计费日期要版本化，区分估算与服务实际计费。

AgentScope 2.0 是已存在的依赖。先用已发布的 event/middleware 与 pipeline，SOP 是实验性能力，独立开关验证再用于业务恢复。旧 Runtime 能力已合入 2.0，不再建立第二个作业控制平面。

### 4.4 多模态检索：真实图片子集有收益再引入

Qwen3-VL Embedding/Reranker 是 2026 年较新的直接候选。先在 DISPIMG、设备铭牌、布局截图子集实验，保存 image ID、父 chunk、sheet/cell 或 page/bbox；图片 collection 与文本 collection 分开，融合候选后保留原门控。

当前 EmbeddingClient 只接受字符串 input，不能仅改模型名称就声称支持图片。不同模型、不同维度乃至同维度不同向量空间都要重建索引，记录模型指纹；文本 baseline 留作对照。视觉召回命中不是安全数值写回的充分条件。

混合检索也是独立实验项：Qdrant [Hybrid Queries](https://qdrant.tech/documentation/concepts/hybrid-queries/) 支持多路 prefetch 和融合。先测设备型号、行标题、数值单位等精确匹配是否是当前 dense 检索的短板，再加 sparse/BM25 与 RRF。仍需保留分层优先级和 namespace filter；不能为了新的术语无证据地宣称“hybrid 一定更准确”。

## 5. 评测设计：把“看起来好”变成可证实的能力

建立版本化 `gold_fields.jsonl`：答案、目标 cell、必须引用的文档/anchor、机房范围、现状/规划、单位、必需槽、是否可回答、上下文是否充分。建议起步人工标注 100–150 个字段、20–30 份代表性资料；这些是建议规模，当前没有新增标注或实测结果。

划分 train/examples、validation、test；按文档/机房分组，而不是随机分散同一张能力表的行。validation 校准写回阈值，test 冻结后再跑。gold 和既有填写答案不进入检索与生成 prompt；格式示例需剥离实例事实。无法留出完整机房时明确报告局限，不能称跨机房泛化。

| 指标 | 定义/验证依据 | 需要防止的误解 |
| --- | --- | --- |
| 字段正确率与状态准确率 | answer/status 对人工 gold，单位和别名规则透明 | 不只在已回答字段上报一个漂亮数 |
| 必需槽覆盖率 | 每个必需槽有正确值且有对应直接证据 | 不能仅判模型填写了 slot_values |
| 检索 Recall@k / nDCG | 全部检索候选对 gold evidence IDs/anchors | 当前引用 ID 的 recall 不等于检索候选 recall |
| 原文定位有效率 | hash、version、quote、区间与原文一致 | 定位通过不代表语义蕴含通过 |
| Grounding precision | 输出主张由正确机房/字段/状态的原文支持 | `bool(source_chunk_ids)` 不足以验证 |
| 写回 precision | 真正写入且答案/目标位置/证据都正确的字段 ÷ 所有实际写入字段 | 无写回时指标未定义，不能记 100% |
| 写回覆盖率 | 实际正确写入字段 ÷ 可回答 gold 字段，同时报告总字段覆盖 | 不能靠多拒答掩盖能力退化 |
| 错误写回率 | 错误写入字段 ÷ 实际写入字段，并附错机房/单位/位置分项 | 分母和 confidence interval 必须明确 |
| 拒答 precision/recall | 不可回答与拒答 gold 二分类 | 不能将所有 partial 视为正确拒答 |
| 风险—覆盖曲线 | 随阈值变化，错误写回风险与覆盖共同变化 | 未校准 confidence 不是概率 |
| 效率 | p50/p95、真实 calls/tokens、单字段/新增正确字段成本 | 缺失 usage 不能填 0，假数据不能当实测 |

建议比较以下组，固定被研究因素以外的数据快照、模型、服务、prompt 和随机性设置，每组保存完整 manifest/config fingerprint。研究 instruction 或模型时只改变对应因素，并明确记录：

1. 原单轮分层 RAG + 上传契约修复，作为所有新组公平的起点。
2. 加 Docling 结构适配；原生/扫描/图片三类分开报告。
3. 加必要的 OCR fallback，记录 OCR 成本和错误。
4. 加实际执行的动作约束与预算；对四工作流分别关闭做消融。
5. 加 quote/span 验证与前端 provenance；原始预测不变，单独测 gate precision/coverage。
6. 再单独比较 Qwen instruction、hybrid 或 Qwen3-VL，避免同时改多个因素无法归因。

上下文不充分 / 充分与答对 / 答错 / 拒答交叉统计，定位检索、解析、生成、引用、写回各环节问题。对概率模型建议重复 3 次、给配对差异及区间；小样本全对也不能宣称“零幻觉”。EvalScope 在离线环境接 artifacts 和自定义指标；LLM judge 可辅助语义评分，人工抽检校准，不能取代权限、来源、坐标、公式和状态不变量。

必须加入的坏例：正确数值但错机房；规划容量当现网；总容量填单台；两个必需槽只覆盖一个；相同数值出现在多行；引用 ID 不存在；OCR 8/3 混淆；设计压力填实测压力；来源冲突；formula/duplicate target/合并单元格；新模板列位置变化；checkpoint 中断恢复。

## 6. 实施顺序和可审查产物

| 阶段 | 具体交付 | 验收证据 | 可展示的面试价值 |
| --- | --- | --- | --- |
| P0 链路与基线 | 上传来源层、真实模板字段分析、版本化索引、领域 gold 与坏例 | 离线可复现端到端；上传 filter 集成；失败回滚旧索引；gold 不泄漏 | 发现并修复跨模块契约，理解真实业务范围 |
| P1 证据工作台 | provenance 校验/旁路产物、Go owner-only 证据读取、字段证据/轨迹 UI | exact/ambiguous/unmatched/hash/version 用例；权限与转义；实际 run 的可点击证据 | 从引用存在到可验证来源，解释拒答与冲突 |
| P2 受控 Agent | 真实 action filter、预算/停止、AgentScope native 事件、calls/tokens trace | 动作改变实际过滤条件；错机房不可扩展；预算耗尽进入 review；恢复一致 | 状态驱动编排、工具约束与成本治理 |
| P3 研究对比 | EvalScope adapter、跨机房 holdout、ablation、风险—覆盖与成本图 | 固定快照、配对结果、人工核验和不确定性报告 | 用实验说明什么有效、什么无效及原因 |
| P4 可选多模态 | Docling/OCR 难例深化、Qwen3-VL 或 hybrid 旁路 | 图片/精确型号子集独立收益与成本，文本 baseline 不退化 | 结合真实失败样本选择新模型和检索路径 |

工作量取决于现有原始资料、模型服务、标注和岗位方向，本文不承诺未经验证的上线时间。第一轮建议只做 P0 + P1 的最小完整链路，随后用评测结果决定 P2/P4 的范围。

UI 美化与技术实现共同推进：统一字号、间距、状态颜色与空/错/加载状态；字段列表→证据→执行过程形成稳定布局；移动端堆叠、键盘可访问；保持原本 owner-only 下载。不要使用没有真实数据的准确率/节省时长大屏，也不将合成数据混入运行记录。

## 7. 面试材料如何讲得可信

建议项目标题：**面向复杂 Office 知识的证据约束工勘填报 Agent 平台**。

可讲的当前能力：Python RAG/字段仲裁，答案与写回策略分离，分层 namespace 检索，Go 异步任务、恢复/取消/进度、对象存储与产物校验，已有四工作流 MAS。当前 AgentScope 集成是受控 callback 编排，部分解析依赖历史产物，需坦诚说明。

迭代完成且实测后再填写简历数字，例如：

> 设计字段级原文证明和写回门控，结合来源约束的多轮检索，在 **[N 个跨机房盲测字段]** 上将写回 precision 从 **[实测 A]** 提高到 **[实测 B]**，同时报告覆盖率、P95 与新增正确字段成本；通过 **[消融结果]** 定位主要收益来自 **[具体环节]**。

所有方括号都是待实测字段，不可直接写成简历成绩。借鉴 Google/阿里研究与自身实现分别说明；引用论文不代表复现了作者训练或指标。无需承诺“零幻觉”“绝对安全”或“全格式支持”。

建议 5 分钟演示：上传一份实际支持的资料和模板→普通可回答字段→总量/单台或错机房坏例→原文高亮与门控理由→一次补检索轨迹→固定实验对比→展示取消/恢复与产物校验。演示公开化之前准备合成或授权脱敏资料，不能直接公开当前私有机房资料和部署凭据。

按岗位侧重：AI 应用重点讲证据与 Agent 实验；算法重点讲检索/充分性、盲测和消融；后端重点讲任务状态机、索引版本切换、资源限制、权限、幂等和可观测性。暂未收到岗位偏好，本轮按 AI 应用/Agent 工程设计。

## 8. 本轮产物与验证范围

- 本文：官方研究筛选、当前代码对应、实施规格、评测与面试路线。
- [来源索引](2026-10-01-sources.json)：获取时间、原始 URL、SHA256 与本地证据路径。GitHub 匿名 REST 限流时，使用官方 raw、HTML、Atom、PyPI、模型卡和 arXiv。
- [证据工作台原型](../../artifacts/design/evidence-workbench.html)：本地字段选择、状态筛选、证据高亮、决策轨迹与评估视图；合成数据，与业务 API 隔离。
- 调研与原型阶段没有改造业务代码；随后已开展代码迭代，具体变更、验证及限制见[实施与验收记录](2026-10-01-implementation.md)。没有调用真实模型、变更线上数据库或发布新 Docker 镜像，也没有业务准确率提升的实验结果。
- 最初原型只完成静态与 DOM 验证。正式 Vue 实现已使用本机 Chrome 完成桌面和移动宽度截图及交互检查；输入为真实 Python→Go 产生的匿名合成契约产物，浏览器 API 被测试拦截，不属于线上部署验收。

首轮实现已从来源契约与证据工作台开始。原始预测与 Overlay 仍分离，引用定位不改变写回门控，保留 formula/duplicate target 保护、owner-only 结果、可恢复任务与 manifest 校验。版本化索引切换、真实新模板分析、完整成本预算及领域盲测等仍需后续实施，不能视为已经完成。
