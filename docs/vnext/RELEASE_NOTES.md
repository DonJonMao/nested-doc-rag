# vNext 发布说明

本次迭代将固定工勘表的 Step15 流水线扩展为由上传知识和上传表单驱动的应用，重点是统一输入契约、定位证据、按缺口补查、安全写回及版本化发布。保留 Go API/Worker、Python Core、Qdrant 和 Vue 架构。

各阶段实现、审计修正与历史验证结果见 [DELIVERY.md](DELIVERY.md) 和 [PHASE_FILES.json](PHASE_FILES.json)。离线发布包的源码版本、服务器架构、镜像身份和校验信息以包内清单为准；历史工程验证记录与本次发布打包记录分别保存。

## 主要变化

| 能力 | 本次行为 | 实现与验证入口 |
| --- | --- | --- |
| 上传知识的统一证据 | 使用 `EvidenceRecord` 区分 `structured_field`、`table_row`、`paragraph` 等记录；生产检索主要按 `evidence_kind` 过滤，保留旧 payload 兼容字段 | [Phase 1](PHASE1.md) |
| 上传表单动态解析 | 当前 Excel 模板生成字段、目标地址与 diagnostics；支持已有用例覆盖的改名、换行、不同目标列和合并类别，显式历史 fallback 有 warning/trace | [Phase 1B](PHASE1B.md) |
| 原文可定位 | 模型只选择本次 evidence ID；文件、sheet、row/cell、Word 段落/表行及引用区间由系统解析，confirmed 引用接受严格校验 | [Phase 2](PHASE2.md) |
| 最多一次缺口补查 | 首轮优先目标结构化证据；充分性对象描述支持项与 `missing_facts`，不足时补查一次，按 ID 合并后再回答或拒答 | [Phase 3](PHASE3.md) |
| 安全写回 | 默认 `preserve` 保护已有人工值和公式；显式策略、实际 old/new value、写回动作及证据进入审计，未安全写入的字段进入 review 清单 | [Phase 4](PHASE4.md) |
| 版本化知识库 | 新版本 build → validate → activate；失败保留 active version，物理向量点身份携带版本，fill 冻结模板及 target/global serving scopes | [Phase 4B](PHASE4B.md)、[迁移设计](PHASE4B_DESIGN.md) |
| Go 任务与 Vue 证据工作台 | API/Worker 编排 Python、归档产物和报告进度/SSE；页面支持字段检索与状态筛选，展示原文、地址、补查过程和实际写回决策 | [Phase 5](PHASE5.md) |

原先两个上传问题已有修复前失败证据及修复后回归：新上传知识曾与生产 retrieval 过滤不匹配；新表单曾无法生成本次字段、仍依赖历史文件。当前新入口通过原生解析连接统一证据与动态字段，三对 fresh 数据的工程链路已经执行。详见 [DELIVERY §4](DELIVERY.md#4-两个原始上传缺陷的前后行为)。

## 审计后修正

- **启动迁移期限。** API/Worker 的迁移入口对数据库锁等待和 SQL 使用30秒整批期限，继承更短调用方期限；真实 PostgreSQL 验证了超时回滚及重试。迁移不会重放旧 seed 来覆盖用户选择。
- **未核准 gold 不参与质量计分。** heldout、候选材料和有限原文核对不能被默认晋升为 gold；质量指标公开可评估分母与 unknown。固定 synthetic 数据保留其已声明的领域规则来源。
- **补查对照只关闭补查。** 新同 A3 primary 对照保留完整首轮候选、预算、sufficiency、生成上下文和 gate。旧 `ablation-stub-03` 还改变首轮证据和充分性检查，其 Answer Gain 不可单独归因补查；旧报告保留，修正后 `stub-04` 单独记录。

具体结果见 [POST_AUDIT_RESULTS.json](POST_AUDIT_RESULTS.json) 和 [EVALUATION_CORRECTIONS.md](EVALUATION_CORRECTIONS.md)。

## 验证结果与边界

| 已有验证 | 记录 |
| --- | --- |
| Python 全量 | 909 passed，0 failures/errors/skips；75条兼容 warning |
| Go race | 523 leaf cases passed；含真实 PostgreSQL 迁移与故障回归 |
| 前端 | 39 cases passed，类型检查与构建通过 |
| 三对 fresh 工程链路 | 30字段完成实际 API/Worker/Python 入库、填表、归档、双校验和下载；模型为明确标注的 stub |
| 写回保护 | 对实际输入与下载 workbook 独立比较，109个原有非空单元格保持不变，包含人工目标值和公式 |
| 历史镜像内容核对 | 此前干净提交的 API/Worker 构建与包源码、配置、迁移文件核对有独立记录；见 [CLEAN_IMAGE_PACKAGING.md](CLEAN_IMAGE_PACKAGING.md)。本次离线包使用自己的版本与镜像清单 |

这些是各自记录时的工程验证结果，不等同于在每个发布镜像内重新执行全部回归。stub 采用受控协议响应，不能证明真实语义充分性、召回质量、正确率提升或跨机房泛化。

真实 embedding、rerank、chat 在多次实际预检中返回 `RemoteDisconnected`。三对真模型 clean Docker、真实 challenge/A0–A3及严格 primary 对照、old141 live blank/preserve兼容复现仍待完成。旧 heldout 与新 native 候选材料尚非质量 gold；独立来源/状态核准是 old141 质量声明的前提，不另设全141人工签审门槛。评测定义与复现命令见 [PHASE5_EVALUATION.md](PHASE5_EVALUATION.md) 和 [PHASE5.md](PHASE5.md)。

## 部署衔接

离线部署包按服务器架构生成，包含应用与基础服务镜像。取得匹配 CPU 架构的包后，解压、进入包根目录并执行：

```bash
./start.sh
```

脚本导入离线镜像并启动平台，首次初始化使用随机管理员密码。支持架构、端口、配置项、密码获取和日常操作见[离线部署指南](../../go-server/deployments/offline/README.md)。启动脚本和镜像打包成功仅证明交付准备与对应检查，不能代替真实模型全链路验收。

Worker 镜像自带 Python Core，API/Worker 与 PostgreSQL、Redis、MinIO、Qdrant 协作，前端静态资源由入口代理提供。离线镜像包不包含模型权重；真实运行需要可达的 embedding、rerank、chat 接口和有效凭据。provider 配置及 `models.env` 按部署指南编辑；带凭据的文件只留在服务器或本地私有包，不提交到 GitHub。

已有库升级需遵循 [迁移说明](PHASE4B_DESIGN.md)：先停止旧 API/Worker 再切换新版本，不混跑新旧二进制。旧索引兼容读取不等于通过新版本的向量校验；target/global serving scopes 当前要求共用 collection。

## 当前保留的限制

可选 schema→value 路径默认关闭，其质量收益需要独立 A4 对比。支持范围以现有原生解析器及回归用例为准，嵌入对象、图片和复杂 Office 布局不能据本次数据集外推为全格式支持。界面用于证据查看及结果下载，人工补充和复核在线下完成。

旧版本与 pins 保守保留，尚无 GC；事件通知尚无 outbox，数据库状态是权威。以上为已说明的运行限制，不增加 GC、outbox或浏览器截图验收门槛。
