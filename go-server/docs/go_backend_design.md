# 数据中心工勘智能填表平台 Go 后端详细设计文档

## 0. 文档定位

本文档用于设计 `nested-doc-rag` 项目的 Go 后端平台。Python 部分已经收束为两类核心能力：

1. **知识库入库核心**：复杂 Office 文档解析、逻辑切分、embedding、Qdrant 入库。
2. **工勘单填表核心**：`Step15AgentRunner overlay mode`，负责 layered RAG、LLM answer arbitration、Agent overlay、review queue、Excel safe writeback、artifact 输出。

Go 后端不重写 Python 的 RAG、LLM、Qdrant、Excel 回写逻辑，而是构建工业级平台服务层，承担：

```text
登录鉴权
知识文档管理
工勘单上传
异步填表任务
Python Core 编排
SSE 进度推送
审核队列
结果下载
任务重试 / 取消 / resume
审计日志
高并发任务治理
可观测性
```

核心原则：

> **Python 是智能引擎，Go 是工业级平台服务层。**

---

## 1. 总体目标

### 1.1 产品目标

构建一个面向数据中心工勘场景的 AI 填表平台，支持：

```text
用户登录
知识库管理
知识文档上传
知识文档入库
工勘单上传
自动填表任务创建
实时查看任务进度
查看待审核字段
下载回填后的 Excel
查看运行日志、trace、summary
```

平台最终形态：

```text
Datacenter Gongkan AI Platform

├── Auth & RBAC
├── Workspace
├── Knowledge Base
├── Document Ingestion
├── Form Filling
├── Review Queue
├── Artifact Center
├── Audit Log
└── Observability / Admin
```

### 1.2 工程目标

后端需要具备以下工程能力：

```text
模块化单体架构
异步任务队列
Worker Pool
长任务状态机
高并发控制
任务取消
任务重试
checkpoint/resume
SSE 实时进度
对象存储
数据库事务
RBAC 权限控制
审计日志
OpenAPI 文档
Prometheus 指标
结构化日志
OpenTelemetry 预留
```

### 1.3 非目标

当前阶段不做：

```text
不用 Go 重写 Python RAG/Agent 核心
不用 Go 直接操作 Qdrant 检索逻辑
不用 Go 直接调用 LLM 生成答案
不做复杂微服务拆分
不做多租户 SaaS 计费
不做完整前端实现
不做 Kubernetes 原生调度
```

---

## 2. 总体架构

### 2.1 系统架构

```text
Frontend
   ↓
Go API Server
   ↓
PostgreSQL + Redis + MinIO
   ↓
Go Worker
   ↓
Python Core CLI
   ↓
Qdrant + Embedding/Rerank/Chat Services
```

### 2.2 组件职责

#### Go API Server

负责：

```text
HTTP API
登录鉴权
RBAC
文件上传
任务创建
状态查询
SSE 连接
审核操作
结果下载
审计日志
```

#### Go Worker

负责：

```text
消费异步任务
调用 Python CLI
监控 Python 运行
读取 checkpoint / manifest
更新数据库状态
归档 artifact
推送 SSE 事件
失败重试
取消任务
```

#### PostgreSQL

负责存储：

```text
用户
角色
工作区
知识库
知识文档
工勘单文件
任务状态
artifact 元数据
审核项
审计日志
```

#### Redis

负责：

```text
任务队列
分布式锁
限流
SSE pub/sub
临时状态缓存
worker heartbeat
```

#### MinIO / S3-compatible Storage

负责：

```text
用户上传文档
工勘单原件
Python 输出 artifact
回写后的 Excel
下载文件
```

#### Python Core

负责：

```text
知识库入库
Step15AgentRunner 填表
Excel 回写
artifact 输出
```

Go 通过以下方式对接 Python：

```text
CLI 调用
run_manifest.json
validate-artifacts
artifact files
```

Go 不依赖 Python 内部函数。

---

## 3. 模块拆分

完整后端拆成 9 个大块：

```text
Block 0：工程骨架与基础设施
Block 1：登录、用户、权限、工作区
Block 2：文件存储与 artifact 管理
Block 3：任务队列、Worker、状态机
Block 4：Python Core 对接层
Block 5：工勘单填表业务
Block 6：知识文档管理与入库业务
Block 7：审核队列与结果回写下载
Block 8：可观测性、安全、运维和压测
```

开发方式：

```text
完整架构先定
接口契约先定
数据库模型先定
artifact contract 先定
然后按模块逐块实现
每块都必须可测试、可验收、可合入
```

---

## 4. 技术选型

### 4.1 推荐技术栈

```text
Language: Go 1.22+
HTTP Router: chi
Database: PostgreSQL
DB Driver: pgx
SQL Layer: sqlc
Migration: goose
Queue: Redis + Asynq
Object Storage: MinIO / S3-compatible
Auth: JWT + Refresh Token
Password Hash: bcrypt or argon2id
Logging: zap or zerolog
Metrics: Prometheus
Tracing: OpenTelemetry 预留
API Spec: OpenAPI 3.0
Config: YAML + ENV override
Container: Docker Compose
```

### 4.2 为什么使用模块化单体

当前阶段不建议直接拆微服务。

原因：

```text
业务边界还在快速演进
部署复杂度要控制
Python Core 已经是独立执行单元
Go 后端主要是平台服务层
模块化单体更利于快速落地和测试
```

后续如有需要，可以按以下边界拆服务：

```text
auth-service
file-service
job-service
knowledge-service
form-filling-service
review-service
```

但第一阶段保持单体。

---

## 5. 项目目录设计

建议目录：

```text
go-server/
  go.mod
  go.sum

  cmd/
    api/
      main.go
    worker/
      main.go

  internal/
    config/
      config.go

    httpx/
      router.go
      response.go
      error.go

    middleware/
      request_id.go
      recover.go
      logger.go
      timeout.go
      cors.go
      auth.go
      rbac.go
      rate_limit.go

    logging/
      logger.go

    database/
      db.go
      tx.go

    redisx/
      redis.go

    storage/
      interface.go
      local.go
      minio.go

    auth/
      handler.go
      service.go
      jwt.go
      password.go
      refresh_token.go

    user/
      model.go
      repo.go
      service.go
      handler.go

    workspace/
      model.go
      repo.go
      service.go
      handler.go

    knowledge/
      model.go
      repo.go
      service.go
      handler.go

    form/
      model.go
      repo.go
      service.go
      handler.go

    run/
      model.go
      repo.go
      service.go
      handler.go
      state_machine.go

    review/
      model.go
      repo.go
      service.go
      handler.go

    artifact/
      model.go
      service.go
      manifest.go
      reader.go
      validator.go

    python/
      runner.go
      command_builder.go
      process.go
      manifest.go
      fake_runner.go

    jobs/
      queue.go
      worker.go
      scheduler.go
      retry.go
      limiter.go

    sse/
      broker.go
      event.go

    audit/
      model.go
      repo.go
      service.go

    observability/
      metrics.go
      tracing.go

  migrations/
    001_init.sql
    002_auth.sql
    003_files.sql
    004_jobs.sql
    005_knowledge.sql
    006_fill_runs.sql
    007_review.sql
    008_audit.sql

  openapi/
    openapi.yaml

  deployments/
    docker-compose.yaml
    Dockerfile.api
    Dockerfile.worker

  tests/
```

---

## 6. Block 0：工程骨架与基础设施

### 6.1 目标

建立标准后端基础能力：

```text
配置加载
日志
路由
统一响应
统一错误码
DB 连接
Redis 连接
MinIO 连接
健康检查
OpenAPI
Docker Compose
测试框架
```

### 6.2 配置设计

配置文件：

```yaml
server:
  addr: ":8080"
  read_timeout: 10s
  write_timeout: 30s
  idle_timeout: 60s

database:
  dsn: "postgres://user:pass@localhost:5432/gongkan?sslmode=disable"
  max_open_conns: 20
  max_idle_conns: 10

redis:
  addr: "localhost:6379"
  password: ""
  db: 0

storage:
  type: "minio" # local|minio
  local_dir: "./runtime/storage"
  minio:
    endpoint: "localhost:9000"
    access_key: "minioadmin"
    secret_key: "minioadmin"
    bucket: "gongkan-platform"
    use_ssl: false

auth:
  access_token_ttl: "30m"
  refresh_token_ttl: "168h"
  jwt_secret_env: "GONGKAN_JWT_SECRET"

python:
  executable: "python"
  project_dir: "../"
  config_path: "config/local.yaml"
  default_timeout: "2h"

jobs:
  fill_concurrency: 2
  ingestion_concurrency: 2
  max_python_processes: 3

cors:
  allowed_origins:
    - "http://localhost:3000"
```

环境变量优先级高于 YAML。

### 6.3 统一响应

成功：

```json
{
  "code": "OK",
  "message": "success",
  "data": {},
  "request_id": "req_123"
}
```

失败：

```json
{
  "code": "INVALID_ARGUMENT",
  "message": "invalid rows format",
  "details": {},
  "request_id": "req_123"
}
```

错误码：

```text
OK
INVALID_ARGUMENT
UNAUTHORIZED
FORBIDDEN
NOT_FOUND
CONFLICT
RATE_LIMITED
INTERNAL
PYTHON_RUN_FAILED
ARTIFACT_VALIDATION_FAILED
```

### 6.4 健康检查

```http
GET /healthz
GET /readyz
GET /metrics
```

`/healthz` 只检查进程活着。

`/readyz` 检查：

```text
PostgreSQL
Redis
MinIO
```

### 6.5 验收标准

```text
go test ./...
docker compose up 后依赖可启动
GET /healthz 返回 ok
GET /readyz 返回 DB/Redis/MinIO 状态
日志包含 request_id
错误响应格式统一
```

---

## 7. Block 1：登录、用户、权限、工作区

### 7.1 目标

实现完整认证授权体系：

```text
用户登录
JWT access token
refresh token
RBAC
工作区权限
审计日志
```

### 7.2 数据表

#### users

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    email TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### roles

```sql
CREATE TABLE roles (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
```

角色：

```text
admin
operator
reviewer
viewer
```

#### user_roles

```sql
CREATE TABLE user_roles (
    user_id UUID NOT NULL REFERENCES users(id),
    role_id UUID NOT NULL REFERENCES roles(id),
    PRIMARY KEY (user_id, role_id)
);
```

#### refresh_tokens

```sql
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### workspaces

```sql
CREATE TABLE workspaces (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### workspace_members

```sql
CREATE TABLE workspace_members (
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    user_id UUID NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
```

### 7.3 API

```http
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/auth/me

POST /api/v1/workspaces
GET  /api/v1/workspaces
GET  /api/v1/workspaces/{workspace_id}
POST /api/v1/workspaces/{workspace_id}/members
GET  /api/v1/workspaces/{workspace_id}/members
```

### 7.4 权限规则

```text
admin:
  全局管理

operator:
  创建知识库
  上传文档
  发起入库
  发起填表

reviewer:
  查看审核队列
  审核字段
  下载结果

viewer:
  只读查看
```

所有业务资源必须检查：

```text
用户是否属于 workspace
用户角色是否允许操作
```

### 7.5 验收标准

```text
用户可登录
JWT 可访问受保护 API
refresh token 可刷新
logout 后 refresh token 失效
RBAC 生效
workspace scope 生效
登录/登出/失败登录写 audit_logs
```

---

## 8. Block 2：文件存储与 Artifact 管理

### 8.1 目标

统一管理：

```text
知识文档上传
工勘单上传
Python 输出 artifact
结果文件下载
```

### 8.2 存储抽象

```go
type ObjectStorage interface {
    Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error
    Get(ctx context.Context, key string) (io.ReadCloser, ObjectInfo, error)
    Delete(ctx context.Context, key string) error
    PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error)
}
```

实现：

```text
LocalStorage
MinIOStorage
```

### 8.3 文件 Key 规范

```text
workspaces/{workspace_id}/documents/{doc_id}/{filename}
workspaces/{workspace_id}/forms/{form_id}/{filename}
workspaces/{workspace_id}/runs/{run_id}/artifacts/{artifact_name}
```

### 8.4 数据表

#### files

```sql
CREATE TABLE files (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    filename TEXT NOT NULL,
    object_key TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    mime_type TEXT,
    sha256 TEXT NOT NULL,
    file_category TEXT NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`file_category`：

```text
knowledge_document
form_template
run_artifact
```

#### run_artifacts

```sql
CREATE TABLE run_artifacts (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    artifact_type TEXT NOT NULL,
    object_key TEXT,
    local_path TEXT,
    content_type TEXT,
    file_size BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 8.5 文件安全

上传时必须：

```text
限制文件大小
检查扩展名
MIME sniffing
计算 SHA256
sanitize 文件名
防止路径穿越
记录上传审计
```

允许类型：

```text
.xlsx
.xls
.docx
.png
.jpg
.jpeg
.pdf 可后续开启
```

暂不建议开放：

```text
.zip
.exe
脚本文件
```

### 8.6 API

```http
POST /api/v1/files
GET  /api/v1/files/{file_id}
GET  /api/v1/files/{file_id}/download
DELETE /api/v1/files/{file_id}
```

### 8.7 验收标准

```text
可以上传 xlsx/docx
可以下载
非法扩展名被拒绝
超大文件被拒绝
同一用户无权限不能下载其他 workspace 文件
MinIO/local 存储可切换
```

---

## 9. Block 3：任务队列、Worker、状态机

### 9.1 目标

所有长任务异步化：

```text
知识库入库
工勘单填表
artifact 归档
```

支持：

```text
队列
worker pool
并发限制
任务取消
失败重试
checkpoint/resume
SSE 进度
状态持久化
```

### 9.2 技术选择

推荐：

```text
Redis + Asynq
```

任务类型：

```text
ingest_knowledge
fill_form
archive_artifacts
```

### 9.3 jobs 表

```sql
CREATE TABLE jobs (
    id UUID PRIMARY KEY,
    job_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id UUID NOT NULL,
    status TEXT NOT NULL,
    attempt INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    payload_json JSONB NOT NULL,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 9.4 run_events 表

```sql
CREATE TABLE run_events (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    event_type TEXT NOT NULL,
    payload_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 9.5 状态机

#### fill run 状态

```text
created
queued
running
succeeded
completed_with_failures
failed
canceled
```

允许流转：

```text
created -> queued
queued -> running
running -> succeeded
running -> completed_with_failures
running -> failed
running -> canceled
failed -> queued, when retry
```

#### ingestion job 状态

```text
created
queued
running
succeeded
failed
canceled
```

### 9.6 并发控制

配置：

```yaml
jobs:
  fill_concurrency: 2
  ingestion_concurrency: 2
  max_python_processes: 3
  max_workspace_running_jobs: 2
```

Go 内部：

```go
type ResourceLimiter struct {
    PythonProcesses chan struct{}
    FillRuns        chan struct{}
    IngestionRuns   chan struct{}
}
```

### 9.7 SSE

API：

```http
GET /api/v1/runs/{run_id}/events
```

事件：

```json
{
  "event": "field_completed",
  "run_id": "...",
  "progress_done": 57,
  "progress_total": 141,
  "answer_status": "partial_clue",
  "review_required": true
}
```

事件类型：

```text
queued
running
field_completed
checkpoint_written
review_item_created
writeback_completed
succeeded
failed
canceled
```

### 9.8 验收标准

```text
任务可入队
worker 可消费
状态流转正确
任务可取消
失败可 retry
worker 重启不丢任务
SSE 能推送进度
```

---

## 10. Block 4：Python Core 对接层

### 10.1 目标

Go 通过稳定边界调用 Python：

```text
CLI
run_manifest.json
validate-artifacts
artifact contract
```

Go 不 import Python。

### 10.2 PythonRunner 接口

```go
type PythonRunner interface {
    RunStep15Agent(ctx context.Context, req Step15RunRequest) (*Step15RunResult, error)
    RunKnowledgeIngestion(ctx context.Context, req IngestionRequest) (*IngestionResult, error)
    ValidateArtifacts(ctx context.Context, runDir string) error
}
```

### 10.3 Step15RunRequest

```go
type Step15RunRequest struct {
    ConfigPath       string
    TargetNamespace  string
    GlobalNamespace  string
    RoomContext      string
    Rows             string
    RetrievalMode    string
    PromptVersion    string
    Judge            bool
    UseJudgeCache    bool
    TemplatePath     string
    Writeback        bool
    Resume           bool
    OutDir           string
}
```

### 10.4 Python 命令

```bash
python -m nested_doc_rag.cli run-step15-agent \
  --config config/local.yaml \
  --target-namespace xixian_4 \
  --global-namespace global \
  --room-context "西咸4号楼 301机房" \
  --rows 4-144 \
  --retrieval-mode layered \
  --prompt-version step15_compat \
  --no-judge \
  --template <template_path> \
  --writeback \
  --resume \
  --out-dir <out_dir>
```

执行后再调用：

```bash
python -m nested_doc_rag.cli validate-artifacts \
  --run-dir <out_dir>
```

### 10.5 进程管理

必须支持：

```text
context cancellation
timeout
stdout/stderr 捕获
进程退出码检查
Python PID 记录
失败错误归类
validate-artifacts 检查
run_manifest.json 解析
```

取消任务：

```text
Go cancel ctx
杀 Python process
任务状态设为 canceled
写 run_events
```

### 10.6 Artifact 读取

Go 后端从 `run_manifest.json` 中定位：

```text
predictions_raw.jsonl
agent_overlays.jsonl
predictions_agent_view.jsonl
review_items.jsonl
trace_summary.json
run_summary.md
filled_form.xlsx
writeback_audit.jsonl
evidence_map.json
```

Go 不假设 artifact 路径结构，只信 manifest。

### 10.7 验收标准

```text
fake PythonRunner 单测通过
真实 Python CLI 可由 Go 启动
cancel 可杀进程
validate-artifacts 失败时任务失败
manifest 解析正确
artifact 元数据入库
```

---

## 11. Block 5：工勘单填表业务

### 11.1 目标

完整实现：

```text
上传工勘单
创建填表任务
异步执行 Python
查看进度
查看结果
下载回写 Excel
```

### 11.2 数据表

#### form_files

```sql
CREATE TABLE form_files (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    file_id UUID NOT NULL REFERENCES files(id),
    filename TEXT NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### fill_runs

```sql
CREATE TABLE fill_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    knowledge_base_id UUID,
    index_version_id UUID,
    form_file_id UUID NOT NULL REFERENCES form_files(id),
    target_namespace TEXT NOT NULL,
    global_namespace TEXT NOT NULL DEFAULT 'global',
    room_context TEXT,
    rows_spec TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_total INT DEFAULT 0,
    progress_done INT DEFAULT 0,
    out_dir TEXT,
    run_manifest_path TEXT,
    summary_path TEXT,
    filled_form_object_key TEXT,
    error_message TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
```

### 11.3 API

```http
POST /api/v1/forms
GET  /api/v1/forms/{form_id}

POST /api/v1/fill-runs
GET  /api/v1/fill-runs
GET  /api/v1/fill-runs/{run_id}
GET  /api/v1/fill-runs/{run_id}/events
POST /api/v1/fill-runs/{run_id}/cancel

GET /api/v1/fill-runs/{run_id}/artifacts
GET /api/v1/fill-runs/{run_id}/download/filled-form
GET /api/v1/fill-runs/{run_id}/download/run-summary
GET /api/v1/fill-runs/{run_id}/download/review-items
GET /api/v1/fill-runs/{run_id}/download/trace
```

### 11.4 创建任务请求

```json
{
  "workspace_id": "ws_xxx",
  "knowledge_base_id": "kb_xxx",
  "form_file_id": "file_xxx",
  "target_namespace": "xixian_4",
  "global_namespace": "global",
  "room_context": "西咸4号楼 301机房",
  "rows": "4-144",
  "judge": false,
  "writeback": true
}
```

### 11.5 任务完成后的处理

Worker 完成 Python 执行后：

```text
读取 run_manifest.json
validate artifacts
上传 artifacts 到 MinIO
写 run_artifacts
解析 review_items.jsonl 入库
更新 fill_runs 统计
推送 SSE run_completed
写 audit log
```

### 11.6 验收标准

```text
工勘单可上传
fill run 可创建
worker 调 Python
任务完成后可查状态
review_items 入库
filled_form 可下载
run_summary 可下载
SSE 可看进度
```

---

## 12. Block 6：知识文档管理与入库业务

### 12.1 目标

实现知识库管理：

```text
创建知识库
上传知识文档
设置 namespace
设置 document_role
触发入库
查看入库状态
管理 index version
```

### 12.2 数据表

#### knowledge_bases

```sql
CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    name TEXT NOT NULL,
    description TEXT,
    qdrant_collection TEXT,
    current_index_version_id UUID,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### knowledge_documents

```sql
CREATE TABLE knowledge_documents (
    id UUID PRIMARY KEY,
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    file_id UUID NOT NULL REFERENCES files(id),
    filename TEXT NOT NULL,
    document_role TEXT NOT NULL,
    namespace TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### knowledge_index_versions

```sql
CREATE TABLE knowledge_index_versions (
    id UUID PRIMARY KEY,
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    version INT NOT NULL,
    qdrant_collection TEXT NOT NULL,
    qdrant_namespace TEXT,
    artifact_dir TEXT,
    manifest_path TEXT,
    status TEXT NOT NULL,
    document_count INT DEFAULT 0,
    chunk_count INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### ingestion_jobs

```sql
CREATE TABLE ingestion_jobs (
    id UUID PRIMARY KEY,
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id),
    index_version_id UUID REFERENCES knowledge_index_versions(id),
    status TEXT NOT NULL,
    progress INT DEFAULT 0,
    error_message TEXT,
    python_command TEXT,
    out_dir TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 12.3 API

```http
POST /api/v1/knowledge-bases
GET  /api/v1/knowledge-bases
GET  /api/v1/knowledge-bases/{kb_id}

POST /api/v1/knowledge-bases/{kb_id}/documents
GET  /api/v1/knowledge-bases/{kb_id}/documents
DELETE /api/v1/documents/{doc_id}

POST /api/v1/knowledge-bases/{kb_id}/ingestion-runs
GET  /api/v1/ingestion-runs/{job_id}
GET  /api/v1/ingestion-runs/{job_id}/events
```

### 12.4 Python 入库 CLI

推荐 Python 后续提供统一入口：

```bash
python -m nested_doc_rag.cli ingest-knowledge \
  --config config/local.yaml \
  --input-dir <uploaded_docs_dir> \
  --namespace xixian_4 \
  --knowledge-base-id <kb_id> \
  --out-dir artifacts/ingestion/<job_id>
```

如果该 CLI 暂未实现，Go 的知识库管理先完成：

```text
知识库元数据
文档上传
文档列表
入库任务状态模型
```

等 Python CLI 准备好后再接入。

### 12.5 验收标准

```text
可以创建知识库
可以上传知识文档
可以设置 namespace/document_role
可以触发入库任务
可以查看入库状态
index version ready 后可被 fill run 选择
```

---

## 13. Block 7：审核队列与回写下载

### 13.1 目标

把 Agent overlay 的生产价值展示出来：

```text
哪些字段需要人工审核
为什么需要审核
是否允许回写
引用了哪些证据
审核人如何处理
```

### 13.2 数据表

#### review_items

```sql
CREATE TABLE review_items (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES fill_runs(id),
    field_id TEXT,
    row_index INT,
    target_cell TEXT,
    question_text TEXT,
    answer_status TEXT,
    answer_value TEXT,
    critic_flags JSONB,
    risk_level TEXT,
    review_required BOOLEAN NOT NULL DEFAULT true,
    writeback_allowed BOOLEAN NOT NULL DEFAULT false,
    reference_source_documents JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_id UUID REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

状态：

```text
pending
approved
rejected
edited
ignored
```

### 13.3 API

```http
GET  /api/v1/fill-runs/{run_id}/review-items
GET  /api/v1/review-items/{item_id}
POST /api/v1/review-items/{item_id}/approve
POST /api/v1/review-items/{item_id}/reject
POST /api/v1/review-items/{item_id}/edit
```

### 13.4 审核操作

#### approve

```json
{
  "comment": "确认可用"
}
```

#### reject

```json
{
  "reason": "证据不足"
}
```

#### edit

```json
{
  "edited_answer": "人工确认后的答案",
  "comment": "根据现场确认修改"
}
```

### 13.5 后续回写策略

第一版：

```text
审核只记录，不二次回写。
```

第二版：

```text
人工编辑后生成 reviewed_predictions.jsonl
调用 Python writeback CLI
生成 reviewed_filled_form.xlsx
```

### 13.6 验收标准

```text
review_items 可查询
可 approve/reject/edit
操作写 audit log
下载结果权限正确
```

---

## 14. Block 8：可观测性、安全、运维和压测

### 14.1 Metrics

Prometheus 指标：

```text
http_request_total
http_request_duration_seconds
fill_run_total
fill_run_duration_seconds
fill_run_failed_total
ingestion_job_total
ingestion_job_failed_total
queue_depth
worker_running_jobs
python_process_running
python_process_failed_total
artifact_validation_failed_total
sse_client_count
```

### 14.2 Logs

结构化日志字段：

```text
timestamp
level
message
request_id
user_id
workspace_id
run_id
job_id
python_pid
duration_ms
error_code
```

### 14.3 Tracing

OpenTelemetry 预留：

```text
HTTP request
DB query
Redis enqueue
Worker consume
Python subprocess
Artifact validation
File download
```

### 14.4 安全

```text
JWT 验证
RBAC
workspace scope
rate limit
body size limit
file type whitelist
download permission
audit log
CORS whitelist
secret via env only
```

### 14.5 压测

压测场景：

```text
并发登录
并发上传
并发创建 fill run
SSE 多连接
多个 worker 并发执行
大文件下载
任务取消
任务失败重试
```

工具：

```text
k6
hey
wrk
```

### 14.6 验收标准

```text
metrics 可被 Prometheus 抓取
结构化日志完整
pprof 可选开启
压测报告可生成
异常任务可追踪
```

---

## 15. 关键业务流程

### 15.1 登录流程

```text
用户提交 username/password
→ auth service 校验密码
→ 生成 access token
→ 生成 refresh token
→ refresh token hash 入库
→ 写 audit log
→ 返回 token
```

### 15.2 工勘单填表流程

```text
用户上传工勘单
→ 文件存储到 MinIO
→ 创建 form_file 记录
→ 用户创建 fill_run
→ API 写 fill_runs 状态 created
→ 任务入队 fill_queue
→ Worker 消费任务
→ 状态变 running
→ Go 构造 Python CLI
→ Python 执行 run-step15-agent
→ Worker 监控 checkpoint / trace
→ SSE 推送进度
→ Python 完成
→ Go 调 validate-artifacts
→ Go 读取 run_manifest
→ artifact 归档到 MinIO
→ review_items 入库
→ fill_run 状态 succeeded/completed_with_failures
→ 用户下载 filled_form.xlsx
```

### 15.3 取消任务流程

```text
用户请求 cancel
→ Go 检查权限
→ 标记 cancel requested
→ cancel Python process context
→ Worker 杀 Python 子进程
→ 状态变 canceled
→ 写 run_events
→ SSE 推送 canceled
```

### 15.4 重试流程

```text
任务失败
→ 判断是否可重试
→ 若可重试，重新入队
→ Python 命令带 --resume
→ 继续原 out_dir
→ 成功后归档 artifact
```

---

## 16. Go 与 Python 的 Artifact Contract

Go 后端必须只依赖这些稳定文件：

```text
run_manifest.json
predictions_raw.jsonl
predictions.jsonl
agent_overlays.jsonl
predictions_agent_view.jsonl
review_items.jsonl
trace.jsonl
trace_summary.json
run_summary.md
summary.json
filled_form.xlsx
writeback_audit.jsonl
evidence_map.json
```

Go 不依赖：

```text
Python 内部函数
Python 类结构
Step15AgentRunner 内部变量
Qdrant 检索实现细节
LLM prompt 细节
```

`run_manifest.json` 是 Go 的入口。

Go 通过：

```bash
python -m nested_doc_rag.cli validate-artifacts --run-dir <out_dir>
```

验证输出完整性。

---

## 17. 开发阶段规划

### Phase 1：Block 0 + Block 1

目标：

```text
工程骨架
数据库
配置
日志
登录
RBAC
Workspace
```

验收：

```text
服务可启动
用户可登录
受保护 API 可访问
workspace 权限生效
```

### Phase 2：Block 2 + Block 3

目标：

```text
文件存储
任务队列
Worker
SSE
状态机
```

验收：

```text
文件可上传下载
任务可入队消费
SSE 可收到进度
任务可取消
```

### Phase 3：Block 4

目标：

```text
PythonRunner
run-step15-agent 调用
manifest 读取
artifact validation
```

验收：

```text
Go 可启动 Python
Python 成功后 Go 读取 artifacts
失败可捕获
```

### Phase 4：Block 5

目标：

```text
完整工勘单填表业务
```

验收：

```text
上传工勘单
创建任务
运行 Python
下载 filled_form
```

### Phase 5：Block 6

目标：

```text
知识库文档管理和入库任务
```

验收：

```text
知识文档上传
入库任务可创建
index version 可管理
```

### Phase 6：Block 7

目标：

```text
审核队列
人工审核
结果下载
```

验收：

```text
review_items 可查询、审核、编辑
```

### Phase 7：Block 8

目标：

```text
可观测性
安全加固
压测
CI/CD
部署
```

验收：

```text
metrics/logs/audit/压测/部署文档完整
```

---

## 18. 测试策略

### 18.1 单元测试

覆盖：

```text
auth
password hash
JWT
RBAC
command builder
artifact parser
run state machine
file validator
storage key builder
review routing
```

### 18.2 集成测试

覆盖：

```text
PostgreSQL repo
Redis queue
MinIO storage
PythonRunner fake
SSE broker
```

### 18.3 API 测试

覆盖：

```text
login
upload file
create fill run
get run status
cancel run
download artifact
review item approve
```

### 18.4 Contract 测试

重点覆盖：

```text
run_manifest.json 解析
artifact 路径读取
predictions_raw + overlays 行数一致
review_items 入库
validate-artifacts 失败处理
```

---

## 19. 部署设计

### 19.1 本地开发

`docker-compose.yaml`：

```text
postgres
redis
minio
go-api
go-worker
```

Python Core 可使用本地源码挂载。

### 19.2 生产部署

初期：

```text
单机 Docker Compose
```

后续：

```text
API 多实例
Worker 多实例
PostgreSQL 独立
Redis 独立
MinIO 独立
Python 环境镜像化
```

### 19.3 环境变量

```text
DATABASE_DSN
REDIS_ADDR
MINIO_ENDPOINT
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
JWT_SECRET
PYTHON_EXECUTABLE
PYTHON_PROJECT_DIR
DEEPSEEK_API_KEY
```

所有 secret 只通过环境变量注入。

---

## 20. 风险与应对

### 20.1 Python 长任务失败

应对：

```text
checkpoint/resume
任务 retry
失败 prediction
artifact validation
run_manifest
```

### 20.2 模型服务不稳定

应对：

```text
Python 侧 retry
Go 侧任务 retry
timeout
失败隔离
状态可见
```

### 20.3 并发压垮模型服务

应对：

```text
worker pool
Python process limiter
workspace limiter
队列限速
任务排队
```

### 20.4 Artifact 格式变化

应对：

```text
docs/contracts.md
validate-artifacts
contract tests
run_manifest.json
```

### 20.5 文件安全

应对：

```text
文件大小限制
扩展名白名单
MIME sniffing
路径穿越防护
对象存储隔离
权限校验
```

---

## 21. 最终平台能力边界

Go 后端负责：

```text
平台 API
用户权限
文件管理
任务调度
进度推送
结果下载
审核队列
审计日志
高并发控制
可观测性
```

Python Core 负责：

```text
知识库入库
工勘单填表
Qdrant 检索
LLM answer arbitration
Agent overlay
Excel 回写
artifact 输出
```

Go 和 Python 的边界：

```text
CLI + out_dir + run_manifest.json + artifacts
```

这是后续稳定演进的核心边界。

---

## 22. 总结

后端不需要先做临时 demo，再补丁式完善。推荐采用：

```text
完整架构设计
模块化生产实现
契约优先
逐块交付
每块验收
```

九个生产模块分别是：

```text
Block 0：工程骨架与基础设施
Block 1：登录、用户、权限、工作区
Block 2：文件存储与 artifact 管理
Block 3：任务队列、Worker、状态机
Block 4：Python Core 对接层
Block 5：工勘单填表业务
Block 6：知识文档管理与入库业务
Block 7：审核队列与结果回写下载
Block 8：可观测性、安全、运维和压测
```

完成后，这个平台可以作为完整的工业级 AI 应用后端：

```text
前端上传知识文档和工勘单
Go 后端管理用户、文件、任务和结果
Python Core 执行 RAG/Agent 填表
平台支持异步任务、SSE 进度、审核队列、Excel 下载、审计和可观测性
```

这条路线既能体现 Go 高并发和后端工程能力，也能保留 Python RAG/Agent 核心的技术深度。

