# vNext 需求与验收证据矩阵

审计日期：2026-10-02（Asia/Shanghai）。本文件是只读发现结果与后续验收要求，不是测试通过报告。本次审计没有运行测试、访问模型接口、启动 Docker 或修改原工作区。

唯一规格为 [NESTED_DOC_RAG_VNEXT_IMPLEMENTATION.md](NESTED_DOC_RAG_VNEXT_IMPLEMENTATION.md)，与 `/Users/mao/Downloads/nested_doc_rag_vnext_implementation_spec.md` 的 SHA256 均为 `5faf68338b22b4ea266e20d1dbb60c15e002dda9cb4d0d74a4d175947d23676a`。基线为 `030300065e0d5041f581f573e772f7756e54abd6`，实现分支为 `feat/evidence-grounded-vnext`，工作树为 `/Users/mao/projects/datacenter-vnext`。原工作区 `/Users/mao/projects/datacenter` 的已有改动与本地资产保持原状。

状态约定：**已观察**仅代表文件、计数或只读命令结果；**待测试**需要执行并保存证据；**待实现/测试**是后续阶段目标。测试数量、退出码和结果由实际执行者补充，不能从本矩阵推断通过。

实现进度更新（2026-10-03）：Phase 0–4B 已有独立执行证据，Phase 5 工程实现与显式替身模型链路已有执行证据。全量 Python、Go race、前端及真实 PostgreSQL 结果见 [PHASE5_RESULTS.json](PHASE5_RESULTS.json)。三对新 KB/表单共 30 个字段从独立空 Docker volumes 开始，完成真实 API/Worker/Python 上传→入库发布→填表→双校验→下载，模型明确为 stub；12 组历史源码 A0–A3 执行及离线评测完成，也明确为 stub。真实 embedding/rerank/chat 两轮探测均断连，真模型效果、三对真模型 Docker 与 old141 live 仍待验收，不能宣称整轮完成。默认 sufficiency 开、A4 关、AgentScope 关、preserve、manifest 1.3。原审计观察与后续运行事实分别记录；见 [PHASE5.md](PHASE5.md)。
## 1. 分阶段需求矩阵

| ID / 阶段 | 规格依据 | 必须满足的行为与输入边界 | 可审计验收证据 | 当前状态 |
| --- | --- | --- | --- | --- |
| B00 / Phase 0 | §0.1、§3 | 在精确基线上冻结旧行为；保留原工作树；先复现两个缺口再修复 | `git status`、branch、HEAD、log、工具版本、生产配置、测试日志及退出码；见 [BASELINE.md](BASELINE.md) | 已验证；见 PHASE0_RESULTS.json |
| B01 / Phase 0 | §3.3 Test A | 用上传 Excel 生成的真实记录进入真实 Qdrant 过滤路径，并按基线 `config/docker.yaml` 查询；不能只测手写 payload 或修改过的计划 | `tests/integration/test_upload_to_retrieval_contract.py` 在基线中暴露“生产计划召回不到上传记录”；保存预期失败原因，不能把其他异常算复现 | 基线确实失败；Phase 1 已转绿 |
| B02 / Phase 0 | §3.3 Test B | 非历史文件名的新模板决定本次字段及真实 target；历史 `form_items.jsonl` 缺失也能驱动新模板 | `tests/integration/test_uploaded_form_to_fields.py` 在基线中暴露历史字段依赖；记录模板、输入路径与具体失败断言 | 基线确实失败；Phase 1B 已转绿 |
| I01 / Phase 1 | §4、M1 | 新上传与旧产物进入统一 `EvidenceRecord`；Excel label/value 可确定时才是 `structured_field`，普通行仍为 `table_row`，表头不能当字段事实 | 上传→解析→索引→生产计划的集成结果；合并表头、空值、图片单元格、公式用例；原始行、列坐标和地址不丢失 | Phase 1 已验证；见 PHASE1.md |
| I02 / Phase 1 | §4.5、兼容规则 | 主查询使用 `evidence_kind`；旧 payload 缺该字段时允许显式 `source_type` fallback；始终保持 namespace、知识库及版本边界 | 新/旧/混合 payload 三类召回断言；fallback trace/warning；错房间与 global 冲突不能通过放宽 filter 消失 | Phase 1 与 4B 已验证；UUID 配对 filter 在 top-k 前应用，legacy 只读 unversioned points |
| I03 / Phase 1B | §5、M2 | 上传 Excel 生成 `FormItem` 和 diagnostics；不固定文件名、行号或“最后一列=答案”；合并分组继承，空问题跳过，公式不成为 target；目标列有歧义时停止 | 改名、换行序、换目标列、多 sheet、合并分组的 fixture；`form_items.jsonl`、`form_parse_report.json` 与当前模板坐标一致 | Phase 1B 已验证；见 PHASE1B.md |
| I04 / Phase 1B | §5.4、执行 Prompt | 显式 `--form-items` 优先；否则由 `--template` 解析本次字段；只有兼容入口可退回旧 artifact | 新模板无历史依赖；legacy fallback 发出 `LegacyFormItemsFallbackWarning` 或等价可识别 trace，并有测试；Go 传参与真实 Python CLI 一致 | Phase 1B 已验证；见 PHASE1B.md |
| E01 / Phase 2 | §6、M3 | LLM 只能选择当前检索包中的 evidence ID；地址由系统 metadata 解析；confirmed 写回至少有一条可定位、source text 非空的 EvidenceRef | 引用 roundtrip、伪造 ID/地址拒绝、文件归属/附件存在性、Excel/Word 坐标验证；新老 artifact 均能验证 | Phase 2 已验证；见 PHASE2.md |
| E02 / Phase 2 | §6.6、§6.7、稳定契约 | 原字段保留并派生兼容；raw prediction 与 overlay 分离；Evidence sheet/comment 可追源；已有逐字定位与哈希、Unicode 区间语义不退化 | `predictions_raw` 不被 overlay 改写；兼容 alias；旧 manifest 不要求新字段；Python→Go→UI 与 workbook audit 对齐 | Phase 2/5 工程已验证；新地址、检索轮次及实际写回值已贯通并通过组件/单元测试；未声称浏览器视觉验收 |
| R01 / Phase 3 | §7、DoD Retrieval | round 0 优先 target structured；sufficient 则不补查；insufficient 列 missing facts 并最多补查一次，按 evidence ID 合并；不足则 partial/not_found 且禁止写回 | 充足/缺口/补查后仍缺口的调用计数与 trace；structured sufficiency object；最多两个 acquisition rounds，不是无限 ReAct；target>global、structured>raw | 工程契约已验证；10 个新增真实 CLI/disk Qdrant 用例使用 localhost 模型替身，真实语义/效果待验收；见 [PHASE3.md](PHASE3.md) |
| R02 / Phase 3B | §8、M5 | 前序稳定后采用 schema→value 的 field-family 约束，复用 Qdrant；不同时更换 embedding/chat/reranker | 同值不同字段的候选与错误字段率；独立 A4（可选）结果，不能混入 A3 的改动解释 | 工程契约已验证；A4 默认关闭，6 个新增 native XLSX/真实 CLI/disk Qdrant 用例采用受控向量与 localhost 模型替身；真实错误字段率/模型效果待验收；见 [PHASE3B.md](PHASE3B.md) |
| W01 / Phase 4 | §9、M6 | 生产默认 `preserve`；非空人工值保留并进入 review，reason=`target_non_empty`；formula 始终保护；`overwrite_all` 只由显式 CLI 使用 | 空值/人工值/公式/合并 target/重复 target 用例；old value、new value、policy、evidence refs、实际 action 审计；`WB_TARGET_NON_EMPTY`、`WB_OVERWRITE_POLICY` 或等价稳定代码 | 工程契约已验证；13 个新增真实 CLI/disk Qdrant 用例、47 个 writer policy 与 16 个 artifact 用例通过；manifest 1.3 核对最终文件/策略一致性，不声称历史 old_value 不可伪造；见 [PHASE4.md](PHASE4.md) |
| K01 / Phase 4B | §10、M7 | build V2→validate count/smoke→原子 activate；失败保留 V1；检索携带 active version；版本必须进入 physical point identity，防止 V2 upsert 覆写 V1 | embedding/upsert/validation/activation 各步失败注入；V1 始终可召回；V2 成功才切换；namespace+version 共用 collection；回收不先删除 active version | 工程契约已验证；native Python/Qdrant + 真实 PG 事务/并发/故障与 frozen fill tests 通过；未实施 GC，旧版本与 pins 保留；见 PHASE4B_RESULTS.json |
| Q01 / Phase 5 | §11、§17 | old 141 可复现；fresh≥3；challenge≥30；A0–A3；完整指标与有效配置 | 版本化 dataset manifest、gold、运行 ledger、原始产物、指标定义、命令与退出码；见下文数量口径 | 三对/30 challenge/58 独立 locator 已固定；12 组实际历史源码运行与离线评测完成，模型 stub；old141 安全输入/索引副本与命令准备完成、old37 replay 通过；真实质量与 old141 live 待验收 |
| D01 / 最终真实 E2E | §17 Reliability、执行 Prompt | 独立 clean Docker storage→API 上传 KB→真实 ingest→上传新表单→解析→检索→回答→写回→归档校验→API 下载；不能靠旧 seed 或历史表单产物成功 | 独立 Compose project/volume 列表、镜像 ID/base commit、API/job/run IDs、SSE、实际 CLI、manifest/validator、下载 workbook 与 audit；明确真实/替身模型标记 | 三对真实 Docker 工程链路通过，模型 stub，0 unsafe overwrite；三对真模型最终验收仍未通过，配置中三个真实接口均断连 |

两次 acquisition rounds 的约束与 round 内分层/schema-value Qdrant 调用数分别记录；多层查询不能伪装成多个补查轮，多个补查轮也不能合并标签后声称只有一轮。不存在通过多项手工线性权重定义的 sufficiency score。

## 2. 字段、地址及兼容输出检查表

| 契约 | 必需保留/新增字段与边界 | 验证方法 |
| --- | --- | --- |
| `EvidenceAddress` | `file_name`、`relative_path`；按来源提供 `sheet_name/table_index/row_index/column_index/cell_range/paragraph_index/source_anchor` | Excel 是实际 A1 范围，Word 是实际段落或 table row；明确内部索引从 0/1 开始的转换；不同文本空间不复用位移 |
| `EvidenceRecord` | `chunk_id/point_id/namespace/knowledge_base_id/evidence_kind/corpus_layer/raw_text/text_for_embedding/address/structural_path/field_name/field_value/proof_attachment_ids/proof_attachments/metadata` | kind 限 `structured_field/table_row/paragraph/document_chunk/document_intro`；embedding 增强文本不能替代原始引用文本；unknown 不自动晋升强证据 |
| `FormItem` | `form_item_id/file_name/sheet_name/row_index/target_cell/category_path/question_text/instruction_text/answer_example/needs_evidence` | target 属于当前上传模板；格式示例与 heldout 答案分开，不能把旧填写答案注入生成输入；歧义不能猜测 |
| 表单 diagnostics | `sheet_count/detected_fields/ambiguous_rows/skipped_rows/target_column_by_sheet` | 每个跳过/歧义可解释；同一 Excel 不因换名改变物理字段含义；fresh 输入不能硬编码 rows 4–144 |
| `EvidenceRef` | `chunk_id/knowledge_base_id/namespace/file_name/relative_path/evidence_kind`，来源地址、`source_text/attachment_ids` | 从当前 hits 解析，而不是模型新造地址；validated KB/file ownership 与实际 source file 一致 |
| 旧引用字段 | `source_chunk_ids/evidence_attachment_ids/reference_source_documents` 等保留，新增 `evidence_refs` 为兼容扩展 | 新旧 API、manifest 与下载方均能读；任何兼容 fallback 显式、可观察、有测试 |
| artifact validator | `EV_REF_NOT_IN_RETRIEVAL/EV_REF_MISSING_ADDRESS/EV_CELL_RANGE_INVALID/EV_FILE_NOT_IN_KB/EV_ATTACHMENT_NOT_FOUND` | 各类失配均有拒绝/降级证据；字段不能只因 ID 非空被算成语义支撑 |
| raw/overlay/manifest | `predictions_raw.jsonl` 保留原始结果，`predictions.jsonl` compatibility alias；overlay、review、trace、writeback audit、manifest 均独立 | 不在后处理偷偷改 raw；字段级失败、partial output、取消/恢复仍真实记账；旧任务缺新增字段仍可读 |
| 安全与原文旁路 | 现有归档归属、路径限制、source hash、quote 区间、permission scope | 用户 A 不读取 B 的来源；Unicode code point `[start,end)` 与 JS UTF-16 明确转换；原文定位成功不等于语义支撑成功 |

输入支持须逐项验证 Python ingestion 与 Go 上传白名单的交集。基线 Python `SUPPORTED_SUFFIXES` 为 `.xlsx/.xlsm/.docx/.txt/.md/.csv`；这不证明所有格式都完成 API→解析→检索→写回，更不证明 PDF、OCR、递归 OLE 或宏保真。Fresh 的表单主验收使用规范要求的 XLSX；Excel 图片/公式等识别用例只证明已测试的操作。

## 3. 已存在资产与 old 141 口径

下表路径均在只读原工作区。vNext 初始化时没有 `data/`、`artifacts/`；不得以此声称原 141 资产不可用，也不得默默把这些资产绑定到 fresh 验收。

| 资产路径（根 `/Users/mao/projects/datacenter`） | 已观察事实 | 允许用途与限制 |
| --- | --- | --- |
| `data/`、`data/工勘单/` | 根目录 14 份 DOCX/XLSX 知识文件，工勘单目录 5 份模板 | 旧 workload 与领域样本；含业务资料，使用副本/只读输入，不公开为演示数据 |
| `artifacts/12_gongkan_form_analysis/form_items.jsonl` | 总共 **563 条**；按 `file_name=基地云机房信息调研表.xlsx`、rows **4–144** 得 **141 条** | 旧141的显式兼容入口；sheet=`华东政务云踏勘横评结果汇总`，target=`G4:G144`；不能将整个563条文件称为141 |
| `data/工勘单/基地云机房信息调研表.xlsx` | 一个 sheet；上述141个 target全部非空，0个 formula；对应 FormItem 的 `answer_example` 139条非空 | 原始值是 heldout/人工值来源；不能覆盖或清空原件以方便测写回 |
| `artifacts/13_gongkan_rag_inputs/rag_question_inputs.jsonl` | 563条历史输入 | 可核对 legacy 输入契约；fresh 不可使用历史字段或 instance facts |
| `baselines/reconstructed_relaxed25_retry5_20260630/` | raw prediction 141条；eval 141条，`heldout_answer`141条非空；raw SHA 与历史运行副本一致 | 可用于离线 artifact/replay 兼容验证与人工 gold 起点；历史答案、judge分数不能直接证明当前真模型效果 |
| `artifacts/runs/reconstructed_relaxed25_retry5_20260630/` | summary记录141完成、0失败、**written/confirmed=37**、review=104；manifest、raw、overlay、eval、filled workbook、audit、evidence map 均存在 | 这是已找到的 writeback-37 历史证据，尚未在vNext重跑。`judge=true`、relaxed gate=true、field-binding agent=false；回放与真实重跑要分别命名 |
| `artifacts/15_vector_store/expanded_ingestion_manifest.jsonl`、`qdrant/collection/datacenter_chunks_v1/storage.sqlite` | manifest summary9533条；只读 SQLite `points`计数9533；旧构建summary声明4096维 Qwen3 embedding | legacy索引存在；版本化迁移需保留兼容，不能假设新schema已写进旧points；只读计数不代表线上Qdrant健康 |
| `artifacts/runs/writeback37_agentic4_closed_book_20260702_merged/` 与 `...monotonic_replay.../` | 同为141字段，但summary分别written=34与39 | 不能仅凭目录名包含37就作为“37写回”精确基线 |
| `artifacts/evaluation/evidence-contract-20261001/`、`scripts/generate_evidence_contract_fixture.py` | 现有五字段匿名契约fixture使用真实本地Qdrant/Step15/writer，但embedding、rerank、answer均替身，部分grounding关闭 | 可以复用证据/归档/UI契约；**不能算fresh真实模型验收、A3模型质量或clean Docker E2E** |
| `artifacts/design/evidence-workbench-live/browser-qa.json`、`docs/research/2026-10-01-implementation.md` | 已有证据UI和浏览器QA记录，输入是匿名合成API产物 | 可复用界面与layout测试；不能把拦截API的浏览器QA当生产上传E2E |

关键资产 SHA256（观察时）：

| 资产 | SHA256 |
| --- | --- |
| 旧141 workbook | `25cd9e4db96139338ffbaa4fdeaa8bca53b3f8360c50a1f5f0395f4ca40134c4` |
| 历史563条 form items | `b41437bf91f39abad0e6f28112b929edb77673bdd3429370bd16d56d3e115199` |
| expanded ingestion manifest | `4ff82b5c0bb782a9fefbaf8c84898fc71c114e97924cd4d7a14235a5a5dd07b0` |
| old37 raw prediction | `adc2a64d63daaf9b111782a7dca296ca8d7bbef2ad8b03a443d4a2fb49a1fbf0` |
| old37 eval results（baselines目录） | `3b09b76626e5217acdd5cfe9c6dca631dbb47c660198acf4704f82b6699d2229` |
| old37 summary（artifacts/runs目录） | `696a6d4cd510b7a2e83add904b1df9ed7ff293e4d226a5ad89f8f9a3c9da5219` |
| old37 writeback audit | `47677ac0a34ff22ee90b02be2c4a572034eb048c1a380397f59b898b45f201b1` |

兼容评测与新安全语义分开：直接把旧非空模板送入默认 `preserve`，写入数下降是预期保护行为，不是应通过强制覆盖修复的“回归”。旧141回答对比保留heldout在评测侧；写回能力另用明确记录生成规则与hash的临时测试副本，或显式兼容policy回放。必须同时测试原模板在preserve下保持人工值。绝不修改原件，也不把heldout泄漏到query/prompt。

## 4. 固定数据集、数量与消融

规格§11、§17的最终门槛优先于末尾Prompt的“测试里至少一个fresh fixture”：最终为 **≥3对新知识库+新表单、≥30个challenge、old141、A0–A3**。三个不同run ID或同一模板改名不能自动算三对独立新输入。

建议三对fresh覆盖不同结构：F1 Excel label/value知识+改名且目标列非G的新表；F2 Word paragraph/table知识+重新排序、多sheet/合并分组的新表；F3 Excel detail与Word混合知识+充足/缺口/人工非空值/公式的新表。输入、gold、expected target/address以及SHA独立固定；每对均从上传开始，不访问旧form items或seed。验收观察实际字段数而不是默认4–144。

Challenge建议分配如下（至少30，不限制继续增加）：

| 类别 | 最低建议数 | 对照断言 |
| --- | --- | --- |
| 同值不同字段 | 5 | 正确field family；不因数值相同引用错字段 |
| 同字段不同机房/房间 | 5 | namespace/room范围不漂移 |
| 现状与规划 | 4 | 规划不能作为当前容量/配置写回 |
| 数字子串干扰 | 4 | 例如单位、子串、总量/单台不能模糊晋升支持 |
| target与global冲突 | 4 | 保持target优先；无法消歧时review |
| table detail补证 | 4 | 支持证据有真实表行/单元格地址 |
| second-round检索 | 4 | sufficient不补查；insufficient只补查一次；无新增/仍缺口则拒答或partial |

每个case有稳定ID、输入事实、gold答案/状态、必需证据与地址、可回答/充分性标注、写回预期。Gold由领域规则/人工核对，LLM judge仅辅助；不能用系统自身引用ID非空当ground truth。按文档/机房分组留出，报告样本限制；不足以证明泛化时不宣称跨机房泛化。

| 组 | 唯一方法增量 | 需要固定的共同条件 |
| --- | --- | --- |
| A0 | 精确base的current writeback-37 pipeline | dataset及gold、embedding/model指纹、chat/rerank、候选预算、prompt与写回policy明确记录 |
| A1 | + unified evidence schema | 同A0；逐项列出不可避免的schema/prompt变化 |
| A2 | + addressable evidence | 同A1；定位/地址有效率与语义正确率分别计算 |
| A3 | + sufficiency-guided targeted retrieval | 同A2；一次primary+最多一次supplement；调用、耗时、证据与答案增量独立记录 |

A4 schema→value只在此前稳定时独立增加。离线预录模型响应适合控制契约变量，但结果要标replay；不能冒充同模型线上重测或准确率提升。真模型结果保存请求/响应标识与有效配置，并说明服务模型漂移/随机性的限制。

必报：Evidence Recall@K、Exact Field Evidence Recall@K、Wrong-Field Retrieval Rate、Target-vs-Global Confusion Rate；Answer Accuracy、Abstention Precision、Unsupported Answer Rate；Writeback Precision/Coverage、Unsafe Overwrite Count；Second-Round Trigger Rate、Evidence Gain、Answer Gain、Average Retrieval Calls/Field。每个指标公开分母、gold依据、过滤条件和失败样本；没有usage时记unknown，不记0。不仅报告已回答子集的accuracy。

审计修正：未核准声明或候选/partial-review材料不进入 gold 派生质量分母，逐项报告 unknown；实际调用/轮数与独立已知输入保护可继续测量。固定 synthetic 领域规则数据保留明确声明的兼容资格，不另加人工签审。Answer Gain 的 A3-primary 必须保留完整首轮检索/预算、sufficiency 与生成上下文，只禁补检；旧 `ablation-stub-03` 多删首轮证据且关闭 sufficiency，不能据其差值单独归因。新 `ablation-stub-04` 已完成严格对照的替身模型工程验证，真实收益仍待实模运行。见 [评测修正](EVALUATION_CORRECTIONS.md)。

## 5. Docker、模型服务与验收分层

Phase 0 只读观察：`docker`位于`/usr/local/bin/docker`，CLI `29.2.1`，Compose `v5.0.2`，client为darwin/arm64、context=`desktop-linux`，当时 daemon 未运行。Phase 4B 后续执行已启动 daemon 并保留原容器/volume；独立 `datacenter-vnext-pg` 使用 PostgreSQL 16.4 与 tmpfs、端口 55432，真实数据库验收通过。环境证据位于 `artifacts/vnext/environment/`。已有历史镜像不包含 vNext，clean upload→fill→download 仍待 Phase 5。

模型配置入口为`config/docker.yaml`、`.env.example`、`src/nested_doc_rag/config.py`；生产使用配置中的embedding、rerank、chat接口，chat密钥env名称为`DEEPSEEK_API_KEY`，Qdrant密钥env为`QDRANT_API_KEY`。原`.env`存在chat密钥配置，但本审计未读取/验证其值，执行环境未继承这些密钥。接口是否可达、鉴权正确、模型版本真实均**未验证**。不能由配置存在推断服务可用。

| 层次 | 实际执行内容 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| 离线契约 | parser、normalizer、filter/本地Qdrant、artifact、writer；显式替身模型或预录响应 | 确定性契约、不变量、失败路径、调用轮数 | 真实embedding召回/语义效果、容器/API链路 |
| Docker契约+替身模型 | 真Go API/Worker、Postgres/Redis/MinIO/Qdrant、真实Python子进程与文件流；仅模型HTTP由显式stub提供 | 容器打包、跨进程、任务、上传、归档、SSE、下载 | 真模型可用性或业务准确率 |
| clean Docker+真模型 | 相同容器链路、隔离空volume、新KB+新表单、实际模型接口、实际Python | 最终部署链路与本次真模型验收结果 | 未测数据的泛化、自动达到某个精度收益 |
| 历史artifact replay/UI mock | 读取旧输出或拦截API响应 | 旧schema读取、展示与layout | 新上传生产执行、clean storage、真模型效果 |

运行ledger至少分别记录`runtime_kind`、`models_kind`、`runner_kind`、`data_origin`、`storage_initial_state`及stub/replay标志、commit、镜像ID、config hash、dataset hash、run/job ID、命令/退出码/日志与产物。单个`actualDocker=true`不足以证明models是真实或runner不是FakeRunner。

现有入口与后续命令（**本阶段未执行**）：

```bash
# 只读环境确认，可重复；当前后三项因daemon未运行而失败
docker --version
docker compose version
docker version
docker ps
docker image ls

# 在vNext工作树执行Phase0测试；需保存期望失败的完整原因与退出码
python -m pytest tests/integration/test_upload_to_retrieval_contract.py -v
python -m pytest tests/integration/test_uploaded_form_to_fields.py -v
```

后续真实Docker阶段使用独立Compose project名和全新的测试volume，保存初始不存在/为空的证明。`go-server/deployments/docker-compose.prod.yaml`、`Dockerfile.api/worker`是现有部署入口。worker镜像中的`.dockerignore`排除了本地data/artifacts；clean验收不注入旧`form_items.jsonl`、历史seed或原索引来掩盖输入问题。验收env/override必须同时处理服务固定的`env_file: .env.prod`和插值，不能只给`--env-file`就假定容器拿到同一配置。

部署命令形态为`docker compose --project-name <独立验收名> --env-file <验收env> -f <验收compose> config --quiet`，再于明确的真实验收阶段build/up；这里不自动执行，也不复用生产volume。端口与project/volume归属必须明确；清理只针对该验收project，不对原部署执行`down -v`。

`go-server/scripts/preflight_prod.sh`包含模型HTTP请求与`compose run worker`，不是纯静态检查，Phase0不要直接运行。`go-server/scripts/smoke_product_flow.sh`可复用表单上传、fill-run、SSE replay和下载步骤，但它要求已有ready KB，**不能单独证明clean storage→KB upload→ingest**；最终驱动器须补齐新建KB、上传文档、实际ingestion、索引状态和版本验证，并保留校验后的下载文件而不是只断言文件非空。

真模型接入后先用三个fresh小数据集验证全链路，再执行old141与challenge/A0–A3。真实调用成本与延迟独立记录；模型不可用时如实标未完成，不能自动用stub替代后仍勾选最终真实验收。

## 6. 完成声明所需证据

所有阶段测试记录、数据集数量/哈希、A0–A3报告、clean Docker真链路日志与下载产物齐备之后才更新DoD。最终交付精确base/final commit、按phase的改动、前后两个输入bug的证据、前后检索流程、安全policy、迁移兼容、未解决限制，以及可复现fresh E2E命令。

当前结论：Phase 0–4B 与 Phase 5 工程实现已有验证，原工作区冻结的 103 个文件未改变。Phase 5 已完成三组 fresh Docker 的替身模型工程链路、30 challenge 固定数据、12 组替身模型 A0–A3 运行/评测与证据 UI；失败/中断尝试保留。真实模型三接口仍断连，真模型 Docker、真实 challenge/A0–A3、old141 live 与 heldout 独立核对待完成，不能据工程测试宣称业务准确率改善或整轮完成。
