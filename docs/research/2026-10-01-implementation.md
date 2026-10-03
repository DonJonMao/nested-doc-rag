# 大厂调研后的首轮技术迭代与界面验收

日期：2026-10-01。选型与官方证据见[研究报告](2026-10-01-interview-upgrade.md)及[来源索引](2026-10-01-sources.json)。本轮保留工作区已有改动，在现有 Python、Go、Vue 架构上落地能力；没有发布服务器或重新构建部署压缩包。

## 已落地的改进

### 1. 上传资料进入真实检索链路

此前上传输出的四种 `uploaded_*` 来源类型全部被默认分层过滤排除。现在代码默认值、默认配置、Docker 配置和本地示例配置都增加 `target_uploaded` 与 `global_uploaded` 层；历史主表优先级保留，上传资料作为独立补充来源。

`ingestion.py` 将提取原文与向量输入分开：原文保留换行、空格与 Unicode，向量文本加入文件/位置说明；记录 UTF-8 文本 SHA256、原文件 SHA256、document_id、parser_type、parser_version 及 Excel 原单元格范围。数字 0 不再因空值判断而丢失。Excel 的来源文本是单元格内容按行序列化后的提取文本，DOCX 是原生段落/表格行文本，不能把这些记录描述成完整 Office 页面渲染。

实际本地 Qdrant 集成测试覆盖 XLSX 行、DOCX 段落、DOCX 表格行与文本片段，并验证：四种来源在四个配置入口都可召回；目标与全局 namespace 保持隔离；其他机房不混入；上传来源没有被改名或标注成历史主能力表强证据。

代码：[ingestion.py](../../src/nested_doc_rag/ingestion.py)、[分层配置](../../config/default.yaml)、[Qdrant payload](../../src/nested_doc_rag/retrieval/qdrant_retriever.py)、[集成测试](../../tests/test_uploaded_retrieval_contract.py)。

### 2. Agent 动作的约束真正执行

`EvidenceRetrievalRole.run_action()` 现在将层级、来源类型传给检索器；生产路径按配置求交集，最终进入 Qdrant 的过滤条件。注入测试检索器也执行同样的 namespace、corpus layer 和来源约束，避免测试假象。

动作只能缩小现有配置，不能自造 namespace 或扩大来源范围；内置补检索动作按实际配置裁剪候选层。空约束返回空结果，未知约束记录 `retrieval_constraint_rejected` 并返回空结果，不因模型提出无效动作而将整个字段的原始结果覆盖为执行失败。

代码：[角色执行](../../src/nested_doc_rag/agent/mas/roles.py)、[计划约束](../../src/nested_doc_rag/retrieval/layered.py)、[Step15 检索入口](../../src/nested_doc_rag/agent/step15_runner.py)。真实 token 预算、计费观测、AgentScope 原生自主工具推理没有在本轮新增。

### 3. 可核对的原文引用与完整产物链路

借鉴 Google LangExtract 的来源定位思路，新增 `evidence_provenance.jsonl` 和 `run_manifest.evidence`；不新增框架依赖。模型逐字 quote 只是候选，Python 在实际检索的原文空间中校验，输出 `exact`、`ambiguous`、`unmatched`、`unavailable`。重复引用不任意选第一次匹配，embedding 文本不冒充原文，声明原文 hash 不一致时无法定位。

区间统一为 Unicode 码点的 `[start,end)`；Python、Go、Vue 都覆盖中文、emoji 和错误区间。新旁路 source_text_hash 为 UTF-8 SHA256 的 64 位十六进制字符串；上传 payload 的 hash 带 `sha256:` 前缀，Python 验证时兼容该声明。来源身份取自实际 hits，模型提供的文件名、单元格等不能伪造权威位置。没有 index_version 时明确记录 `unknown`，这不证明索引新鲜度。

旁路覆盖所有处理字段，包括失败和未找到；checkpoint 支持恢复，旧检查点缺定位数据会明确提示。最终写回动作从真实 Excel audit 获取，引用定位不会改变原始预测、Overlay 或写回门控。**定位成功只证明一段字存在于该源文本，不证明答案受到语义支持或允许写回。**

Go 的详情接口读取已归档 manifest 并输出 typed evidence，检查 SHA256、计数、文本空间、区间与唯一匹配。坏 manifest 或缺归档产物不暴露证据。读取保持 owner-only，并校验 artifact 的 RunID/WorkspaceID；Python 内部 `step15_agent_*` 标识与 Go UUID 是不同标识。旧任务返回空 evidence，前端可继续查看原有写回来源。

归档同时拒绝绝对路径、路径逃逸、符号链接逃逸与非文件；图片下载兼容新 evidence 和旧 writeback 引用，仍需所有者权限、manifest 声明及归档归属。

代码：[Python 原文定位](../../src/nested_doc_rag/grounding/provenance.py)、[Go 校验](../../go-server/internal/python/evidence.go)、[Go 详情](../../go-server/internal/form/result.go)、[定位测试](../../tests/test_evidence_provenance.py)、[跨语言与权限测试](../../go-server/tests/fill_evidence_test.go)。

### 4. 真实 Vue 证据工作台

任务详情已从“只列存疑字段”改为查看全部归档字段：搜索字段/答案/单元格，按写回状态、未找到和处理失败筛选；左侧选择字段，右侧展示原始答案、实际写回动作、门控理由、独立来源和逐字高亮。未找到的原始回答即使最终被 flagged，也能进入未找到筛选。

Vue 使用文本节点和 `<mark>`，再次验证 Unicode 区间与 quote，避免原文 HTML 被执行；区间无效或多处匹配不高亮。旧任务明确显示缺原文定位记录；空、失败、进行中状态有对应提示。支持方向键、Home/End、焦点样式以及移动端堆叠。已有 SSE/轮询、取消、下载、运行事件时间线继续使用。

详情页将九宫格指标收敛为四个主要结果指标与紧凑状态条，区分实际写入和门控许可；缺实际进度时显示等待进度，不再填入虚构百分比。

代码：[工作台](../../web/src/components/fill/EvidenceWorkbench.vue)、[展示与高亮逻辑](../../web/src/components/fill/evidenceWorkbench.ts)、[任务详情](../../web/src/views/FillRunDetailView.vue)、[前端测试](../../web/tests)。

## 实际验证与可复用演示

Python 全量 `pytest -q`：250 项通过。Go 全量 `go test ./...` 通过。修改的 Python 文件 Ruff 检查通过；前端 23 项逻辑/组件/异步任务隔离测试、类型检查和生产构建通过。

使用[合成产物生成脚本](../../scripts/generate_evidence_contract_fixture.py)运行真实 local Qdrant、Step15、checkpoint、manifest 和 Excel 写回，再通过实际 Go archiver、artifact.Service 和 FillRunService 输出[详情 JSON](../../artifacts/evaluation/evidence-contract-20261001/fill_run_detail.json)。Go 测试中的 repository 与对象存储使用本地替身，浏览器只拦截 API 数据，不修改线上服务。

```bash
python scripts/generate_evidence_contract_fixture.py
cd go-server
EVIDENCE_CONTRACT_FIXTURE_DIR="$PWD/../artifacts/evaluation/evidence-contract-20261001" go test ./tests -run '^TestPythonEvidenceContractFixture$' -count=1 -v
```

样例是匿名合成资料，embedding、rerank、回答均使用确定性替身，模型和外部网络调用为 0。五个字段覆盖中文 emoji 唯一引用、重复引用、非逐字引用、不存在的 chunk 和未找到答案；十个引用的定位计数是 exact 1、ambiguous 1、unmatched 1、unavailable 7。额外 unavailable 包括没有候选 quote 的关联线索，计数单位是引用，不是字段。

合成样例为隔离验证位置与写回的关系而关闭额外 grounding 检查，effective_config 与 metadata 随产物保存。样例中唯一引用字段实际写入，重复引用的 partial_clue 进入存疑策略，非逐字引用的 answered 字段按原门控实际写入，缺来源和未找到字段进入复核。这明确验证**旁路定位不会偷偷改写已有门控**，不能用来证明引用失配会自动阻止写回或系统已达到严格业务精度。

本机 Chrome 154 的真实页面检查通过：字段全量展示、emoji 后高亮、键盘选择、重复引用不高亮、未匹配引用与写回动作分离、未找到筛选、单元格搜索、空筛选结果，以及 768/390/320 像素布局无水平溢出。浏览器没有 JavaScript 异常或外部请求。[浏览器验收记录](../../artifacts/design/evidence-workbench-live/browser-qa.json)。

截图使用上述匿名合成产物：[桌面页面](../../artifacts/design/evidence-workbench-live/desktop-page.png)、[桌面引用视图](../../artifacts/design/evidence-workbench-live/desktop-exact.png)、[重复引用](../../artifacts/design/evidence-workbench-live/desktop-ambiguous.png)、[手机视图](../../artifacts/design/evidence-workbench-live/width-390-exact.png)。

## 面试时可讲与暂不能讲的内容

可讲：发现上传与生产过滤的跨模块契约错误；将受约束动作从 trace 标签落实到实际查询；设计不侵入原始预测的定位旁路；跨语言统一 Unicode/哈希；用 owner-only、归档归属和路径校验约束证据暴露；通过真实产物驱动的界面让复核者看到“回答、写回、引用”三个不同维度。

建议演示顺序：一个唯一可定位引用 → 重复引用待消歧 → 引用失配但原门控行为保持可见 → 缺来源进入复核 → 未找到筛选 → 旧任务兼容与真实 Excel audit。这些例子展示工程不变量，不作为模型准确率成绩。

暂不能讲：真实机房字段准确率提升、成本下降百分比、跨机房泛化、完整 LangExtract/Docling/EvalScope 复现，或者原生多 Agent 自主协商。本轮只借鉴已核实的方法，并没有把厂商榜单搬成本项目指标。

研究路线中仍待实施：真实新模板字段分析、版本化索引构建/切换/回滚、领域人工 gold 与跨机房盲测、真实调用/usage 预算、逐轮动作 UI，及按坏例需要接入 Docling/OCR/多模态。它们是后续候选迭代；本轮没有声称实现整张路线表。

部署方面：当前已有 2026-07-06 ARM64 压缩包是旧构建，**不包含本轮改动**。要部署这次迭代，应在完成目标服务器配置和真实业务验收后重新构建镜像及部署包。
