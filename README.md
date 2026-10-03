# 工勘智能填表 · Nested Doc RAG

面向数据中心工勘资料的文档检索与 Excel 自动填表平台。管理员上传知识文档，用户选择数据中心和机房、上传自己的表单；系统从资料中寻找证据，生成回答并按安全策略写回 Excel，同时提供来源定位、处理过程和人工补充清单。

项目由 **Python 文档与 RAG 核心、Go API/任务 Worker、Vue 3 证据工作台**组成，使用 Qdrant、PostgreSQL、Redis 和 MinIO。当前迭代的重点是让新上传的知识和表单真正驱动任务，并让每次回答、补查和写回都有可检查的依据。

## 技术亮点

1. **统一上传知识的证据契约。** 将已实现解析器支持的 Excel、Word 和文本资料归一为 `EvidenceRecord`，区分结构化字段、表格行和段落。检索主要按 `evidence_kind` 过滤，原文、检索文本和来源地址分别保存，同时保留旧 payload 的兼容字段。
2. **从当前表单动态解析字段。** 上传的 Excel 模板决定本次 `FormItem`、目标单元格和解析诊断；生产入口不固定历史文件名、4–144 行或“最后一列就是答案列”。
3. **回答能回到具体原文。** 模型选择本次检索到的证据 ID，系统根据元数据解析文件、sheet、row/cell、Word 段落或表行，并记录原文引用区间。确认写回需通过引用校验；地址定位与事实是否支持答案分别判断。
4. **证据不足时最多补查一次。** 首轮优先检索目标库的结构化证据，使用包含 `missing_facts` 的充分性对象判断是否需要补查。只有不足时才构造缺口查询，再合并、检查、回答或拒答；trace 记录触发理由、检索轮次和实际查询次数。
5. **安全写回与可核对审计。** 默认 `preserve` 保留已有人工值，公式继续保护；审计记录实际 old/new value、policy、action 和证据。证据不足、冲突或处理失败的字段进入人工补充清单，用户下载后线下复核。
6. **知识库按版本构建和发布。** 新版本先 build、validate，再原子 activate；失败保留当前可用版本。版本进入向量点身份，填表任务冻结知识版本和模板，避免更新或重试时悄悄换用另一套输入。
7. **后台任务与前端证据工作台贯通。** Go 负责认证、文件、任务队列、Python 子进程、进度/SSE 和产物下载；Vue 展示字段状态、原文与地址、检索路径、缺失事实及实际写回动作。浏览器关闭后后台任务仍可继续。

## vNext 本次修改

| 原始问题 | 本次实现 |
| --- | --- |
| 新上传 Excel 已入向量库，但与生产分层检索的过滤契约不一致 | 统一证据 schema 与检索过滤，区分结构化字段和普通表格行 |
| 新表单没有生成自己的字段，仍依赖旧 `form_items.jsonl` | 从上传模板生成本次字段与地址，历史入口仅保留为显式兼容路径 |
| 回答引用、补查决策和写回结果难以逐项核对 | 增加可定位证据、一次缺口补查、原值保护、版本冻结和证据工作台 |

本次还修正了迁移超时、未核准 gold 的计分资格和补查消融的 primary 对照。详见[发布说明](docs/vnext/RELEASE_NOTES.md)、[分阶段交付与验证边界](docs/vnext/DELIVERY.md)及[完整变更文件清单](docs/vnext/PHASE_FILES.json)。

## 已验证范围

工程回归已有 **909 项 Python、523 项 Go race leaf、39 项前端测试**通过，另有前端类型检查/构建与真实 PostgreSQL 迁移测试记录。三对新知识库/表单、共30个挑战字段已走通实际 API → Worker → Python → 入库/填表 → 产物校验 → 下载链路，**这些运行的模型明确为 stub**。

配置中的真实 embedding、rerank、chat 服务在实际预检中仍断连，因此真实模型召回、答案质量、补查收益和 old141 live 尚未验收。上述测试与打包结果不代表业务准确率或性能提升百分比。运行记录分别保存在[Phase 5](docs/vnext/PHASE5.md)、[审计后结果](docs/vnext/POST_AUDIT_RESULTS.json)和[评测修正](docs/vnext/EVALUATION_CORRECTIONS.md)。

## 部署与使用

获取与服务器 CPU 架构匹配的离线包，解压并进入包根目录，执行：

```bash
./start.sh
```

脚本导入包内应用与基础服务镜像并启动平台，首次初始化使用随机管理员密码；配置与密码获取方式见[离线部署指南](go-server/deployments/offline/README.md)。Worker 自带 Python Core，真实填表仍需配置可达的 embedding、rerank、chat 服务。`models.env` 留在服务器或本地私有包，按部署指南设置 provider 和凭据；离线包不含模型权重。

本次发布变化见[发布说明](docs/vnext/RELEASE_NOTES.md)。源码开发入口继续保留在下方 Quickstart、[Go 服务说明](go-server/README.md)和[前端说明](web/README.md)。

用户流程：管理员上传知识并等待分库 ready → 用户选择分库与机房、上传 `.xlsx` → 后台执行 → 查看证据与写回结果 → 下载表格和人工补充清单。知识库管理为管理员功能，填表任务及下载按创建者权限访问；人工补充在下载后完成。

`Step15AgentRunner` 是主要运行入口。评测读取未被 overlay 改写的 raw prediction，trace/review/writeback 作为附加控制和产物；`FieldFillingAgent` 与历史脚本保留用于兼容或实验。可选 schema-first 默认关闭，AgentScope/MAS 默认关闭。实验设计见 [Agentic Evidence-Replanning MAS](docs/agentic_evidence_replanning_mas_design.md)。

## Quickstart

Install:

```bash
python -m pip install -e .
```

Run tests:

```bash
pytest
```

Inspect configuration:

```bash
python -m nested_doc_rag.cli show-config --config config/local.example.yaml
```

## Gongkan Platform App

The productized Gongkan platform lives alongside the Python Core:

- `go-server/` provides the API server, auth/RBAC, workspace/file/artifact services, jobs, worker orchestration, SSE, knowledge-base ingestion APIs, fill-run APIs, and OpenAPI.
- `web/` provides the Vue 3 app for role-based login, admin knowledge management, user form upload, fill-run creation, SSE progress, task history, result summaries, and artifact downloads.

Current product MVP is a download-oriented automatic form-filling flow. The system writes confirmed safe fields into `filled_form.xlsx`. Optional uncertain writeback is conservative and disabled by default; when enabled, evidence-backed uncertain fields are written only to empty target cells with red styling and an `[UNCERTAIN]` evidence comment. Unsafe, evidence-insufficient, conflict, or flagged fields are exported through `review_items`, and users complete or verify those fields offline after downloading the workbook.

User flow:

1. Create a fill task from a ready knowledge base and an uploaded form template.
2. Wait for the worker to finish Python Step15.
3. Download `filled_form.xlsx`, the safe automatically filled workbook.
4. Download `review_items.csv` to see fields that need offline manual completion.
5. Complete the remaining fields manually outside the platform.

The current MVP does not provide online field approval, online spreadsheet editing, human-review re-writeback, `reviewed_filled_form.xlsx`, or multi-level approval workflow.

Current permission model:

- Knowledge-base management is admin-only. Ordinary users can only read ready knowledge-base options for creating fill tasks.
- Fill tasks, progress, result summaries, SSE events, and downloads are owner-only by `fill_runs.created_by`. Admin users also see only their own fill tasks by default.
- `workspace_id` is retained for compatibility and resource grouping. It is not a sharing boundary for fill-run visibility; users in the same workspace cannot see each other's fill tasks or downloads.

The web app calls the real Go API. Long-running ingestion and form-filling tasks are executed by the Go worker through Python Core CLI entry points; the frontend does not mock task success.

Optional Model Gateway:

- Disabled by default, so Python Core keeps using configured direct chat/embedding/rerank endpoints.
- When enabled, worker-injected `NDR_MODEL_GATEWAY_*` env routes Python Core model calls through `POST /internal/model-gateway/v1/chat/completions`, `/v1/embeddings`, and `/v1/rerank`.
- Gateway enforces bounded concurrency, queues, per-run inflight limits, finite retry, circuit breakers, internal token auth, stats, and request tracing without logging full prompts, evidence, or answers.

Run platform tests and builds:

```bash
cd go-server && go test ./...
cd ../web && npm install && npm run build
```

Run mini baseline smoke tests:

```bash
python -m nested_doc_rag.cli run-baselines \
  --config experiments/form_filling_baselines.yaml \
  --out-dir artifacts/experiments/baselines
```

Run on an uploaded form template with configured real services (replace the template path and namespaces):

```bash
# Step15 retrieval plan is layered by default.
python -m nested_doc_rag.cli run-step15-agent \
  --config config/local.yaml \
  --target-namespace xixian_4 \
  --global-namespace global \
  --room-context "西咸4号楼 301机房" \
  --template path/to/uploaded-form.xlsx \
  --rows all \
  --prompt-version step15_compat \
  --no-judge \
  --resume \
  --out-dir artifacts/runs/step15_agent_overlay
```

Run with Excel writeback:

```bash
# Step15 retrieval plan is layered by default.
python -m nested_doc_rag.cli run-step15-agent \
  --config config/local.yaml \
  --target-namespace xixian_4 \
  --room-context "西咸4号楼 301机房" \
  --rows all \
  --prompt-version step15_compat \
  --no-judge \
  --template path/to/uploaded-form.xlsx \
  --writeback \
  --resume \
  --out-dir artifacts/runs/step15_agent_writeback
```

Uncertain writeback remains off unless explicitly configured:

```yaml
writeback:
  allow_uncertain: true
  uncertain_style: red_fill
  uncertain_comment_prefix: "[UNCERTAIN]"
  embed_evidence_images: true
  evidence_image_mode: append_sheet
  max_evidence_images_per_field: 3
  max_comment_chars: 2000
  evidence_image_max_width_px: 360
  evidence_image_max_height_px: 240
```

Written cells include a compact evidence comment with only the source document name and recalled text block. When uncertain writeback is enabled, uncertain cells are also marked red and use the same evidence comment format with the `[UNCERTAIN]` prefix. Image proofs are extracted during knowledge ingestion or Step 11 manifest build into `evidence_images/` plus `proof_attachment_registry.jsonl`; writeback resolves `attachment_id` through that registry and appends the image proofs at the end of the `Evidence` sheet. If an old index has not been rebuilt yet, writeback still falls back to the source workbook `DISPIMG` media lookup.

Validate an output directory:

```bash
python -m nested_doc_rag.cli validate-artifacts \
  --run-dir artifacts/runs/step15_agent_overlay
```

## Outputs

Stable Step15AgentRunner overlay artifacts:

- `predictions_raw.jsonl`
- `predictions.jsonl`
- `agent_overlays.jsonl`
- `predictions_agent_view.jsonl`
- `review_items.jsonl`
- `trace.jsonl`
- `trace_summary.json`
- `run_summary.md`
- `summary.json`
- `run_manifest.json`
- `evidence_provenance.jsonl`, field-level original-text quotation locations for new runs
- `filled_form.xlsx`, when writeback is enabled and completed
- `writeback_audit.jsonl`, when writeback is enabled and completed
- `evidence_map.json`, when writeback is enabled and completed
- `image_evidence.jsonl`, when image proof references are present

New vNext runs use `run_manifest.json` with `schema_version=1.3`, including frozen scope/input/writeback declarations, `writeback.summary`, and field-level `status`, `answer_status`, `answer_value`, `writeback_action`, and `evidence_refs[]`. Older manifests remain readable through the legacy compatibility path. `predictions.jsonl` is a compatibility alias of `predictions_raw.jsonl` in overlay mode. Go/backend integrations should use `run_manifest.json` to locate artifacts and should not depend on Python internal functions.

New runs also include an optional `evidence` block and an `artifacts.evidence_provenance` path. Quotation locations use Unicode code-point intervals in extracted original text and distinguish `exact`, `ambiguous`, `unmatched`, and `unavailable`. Location success does not establish semantic support or change the existing writeback policy. Old manifests remain readable without this block. The Vue run detail shows these records alongside the answer and actual Excel writeback action.

See [docs/contracts.md](docs/contracts.md) for the frozen artifact contract.

Official-source research and the implemented iteration are documented in the [2026-10-01 research report](docs/research/2026-10-01-interview-upgrade.md) and [implementation and verification record](docs/research/2026-10-01-implementation.md). The existing July ARM64 deployment archive predates this iteration; deployment of the new code requires rebuilding it.

The [Chinese PDF brief](output/pdf/datacenter-research-upgrade-proposals-20261001.pdf) summarizes official-source selection, eight prioritized proposals, acceptance criteria, evaluation design, UI screenshots, and interview guidance.

## Remote Services

Real runs require locally configured services:

- embedding endpoint
- rerank endpoint
- DeepSeek/OpenAI-compatible chat completion endpoint
- Qdrant local path or server configuration. Leave `qdrant.url` empty to use the embedded local path under `paths.qdrant_path`; set `qdrant.url` such as `http://localhost:6333` to use a Qdrant service.

Service URLs belong in `config/local.yaml`, environment variables, or CLI flags. API keys are read through environment variables such as `DEEPSEEK_API_KEY`. Do not commit `config/local.yaml`, `.env`, generated artifacts, vector stores, source data, or secrets.
