# Docker 生产化改造设计文档：Worker 自包含 Python Core

## 0. 分支策略

建议从当前 `agentic-rag` 分支新开一个独立分支：

```bash
git checkout agentic-rag
git pull origin agentic-rag
git checkout -b ops/docker-worker-python-core
```

分支目标只解决 Docker 生产部署问题，不混入检索、索引蓝绿发布、在线复核、并发调度等业务改造。

---

## 1. 背景与问题定义

当前系统的生产运行链路是：

```text
Go API      ：接收用户请求、认证、文件、任务、下载、SSE
Go Worker   ：消费任务队列、编排 Python 子进程
Python Core ：执行 knowledge ingestion、Step15 RAG、artifact validation
Postgres    ：业务元数据、任务状态、用户、权限
Redis       ：Asynq 队列、事件桥、运行事件
MinIO       ：上传文件和产物归档
Qdrant      ：向量索引
```

当前 Worker 的生产镜像存在根本性断点：

```text
Go Worker 运行时会调用：
python -m nested_doc_rag.cli ingest-knowledge
python -m nested_doc_rag.cli run-step15-agent
python -m nested_doc_rag.cli validate-artifacts

但现有 Dockerfile.worker 只构建 Go binary，没有 Python，也没有 nested_doc_rag 包。
```

因此当前状态是：

```text
本地宿主机运行 worker 可以成功，因为宿主机有 Python 环境。
Docker 容器运行 worker 很可能失败，因为容器内没有 Python Core。
```

典型报错包括：

```text
exec: "python": executable file not found
ModuleNotFoundError: No module named 'nested_doc_rag'
python project dir not found: /app/python-core
config/local.yaml not found
```

---

## 2. 改造目标

本次改造的目标是让项目可以在服务器上通过 Docker Compose 持久化启动：

```bash
cd go-server
cp deployments/.env.prod.example deployments/.env.prod
# 修改 .env.prod 中的密码、JWT secret、模型 API key 等
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod up -d --build
```

启动后应满足：

```text
1. API 容器稳定启动。
2. Worker 容器稳定启动。
3. Worker 容器内存在 Python 3.11。
4. Worker 容器内可以 import nested_doc_rag。
5. Worker 容器内可以执行 python -m nested_doc_rag.cli --help。
6. Worker 容器内可以执行 show-config。
7. Postgres、Redis、MinIO、Qdrant 使用持久化 volume。
8. 容器重启后，数据库、对象存储、向量库数据不丢。
9. 默认并发保持保守配置，适合少用户企业内部试点。
```

---

## 3. 非目标

本分支不解决以下问题：

```text
1. 不改 RAG 检索算法。
2. 不改 Step15 artifact contract。
3. 不实现 Qdrant 索引蓝绿发布。
4. 不实现在线 Review Center。
5. 不实现多租户复杂并发治理。
6. 不默认启用 Model Gateway。
7. 不引入 Kubernetes。
8. 不重构 Python Core 为独立服务。
```

这次改造只做一个清晰、可部署、可验证的生产 Docker 基座。

---

## 4. 总体方案

采用“一体化 Worker 镜像”：

```text
API 镜像：
- Go API binary
- configs
- migrations
- 不包含 Python Core

Worker 镜像：
- Go worker binary
- Python 3.11 runtime
- nested_doc_rag Python wheel
- configs
- migrations
- Python docker config
```

为什么先采用一体化 Worker，而不是拆 Python Core 服务：

```text
1. 当前 Go Worker 已经通过 subprocess 调 Python CLI，改造成本最低。
2. 单服务器部署最简单。
3. 出问题时日志链路最短。
4. 避免引入新的服务发现、鉴权、RPC、重试、超时协议。
5. 当前用户量较少，Worker 横向扩容不是首要问题。
```

目标拓扑：

```text
                ┌──────────────┐
                │   Browser    │
                └──────┬───────┘
                       │ HTTP/SSE
                       ▼
                ┌──────────────┐
                │   Go API     │
                └──┬────┬──────┘
                   │    │
          Postgres │    │ Redis / Asynq
                   │    │
                   ▼    ▼
              ┌──────────────┐
              │  Go Worker   │
              │ + PythonCore │
              └──┬────┬──────┘
                 │    │
           MinIO │    │ Qdrant
                 ▼    ▼
```

---

## 5. 文件级改造清单

### 5.1 新增根目录 `.dockerignore`

目的：Worker build context 改成仓库根目录后，必须避免把数据、产物、密钥、前端 node_modules、历史向量库等内容发进 Docker build context。

建议新增：

```dockerignore
.git
.github

# Python caches
__pycache__/
*.py[cod]
*.pyo
*.pyd
*.egg-info/
.pytest_cache/
.ruff_cache/
.mypy_cache/

# Local/generated data
data/
artifacts/
tmp_ole_probe/
qdrant/
*.sqlite
*.sqlite3
*.db

# Secrets and local envs
.env
.env.*
*.key
*.pem

# Go build outputs
go-server/api
go-server/worker
go-server/gongkan-api
go-server/gongkan-worker

# Frontend local dependencies/build outputs
web/node_modules/
web/dist/
web/test-results/
web/playwright-report/

# Logs/temp
*.log
*.tmp
*.bak
.DS_Store
```

注意：不要排除以下文件，否则 Worker 镜像无法构建：

```text
pyproject.toml
README.md
src/
config/
go-server/
```

---

### 5.2 替换 `go-server/deployments/Dockerfile.worker`

目标：构建一个包含 Go Worker 和 Python Core 的 runtime 镜像。

建议内容：

```dockerfile
# syntax=docker/dockerfile:1

# ----------------------------
# 1. Build Go worker
# ----------------------------
FROM golang:1.22 AS go-builder

WORKDIR /src/go-server

COPY go-server/go.mod go-server/go.sum ./
RUN go mod download

COPY go-server ./

RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags="-s -w" \
    -o /out/gongkan-worker ./cmd/worker


# ----------------------------
# 2. Build Python wheels
# ----------------------------
FROM python:3.11-slim AS py-builder

WORKDIR /src/python-core

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip wheel --wheel-dir /wheels .


# ----------------------------
# 3. Runtime image
# ----------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GONGKAN_PYTHON_EXECUTABLE=/usr/local/bin/python \
    GONGKAN_PYTHON_PROJECT_DIR=/app/python-core

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=go-builder /out/gongkan-worker /app/gongkan-worker

COPY --from=py-builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

COPY go-server/configs /app/configs
COPY go-server/migrations /app/migrations

# The Python CLI receives --config config/docker.yaml while its cwd is /app/python-core.
RUN mkdir -p /app/python-core/config
COPY config/docker.yaml /app/python-core/config/docker.yaml

RUN mkdir -p \
      /app/runtime/tmp/uploads \
      /app/python-core/artifacts \
      /app/python-core/tmp \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser \
    && chown -R appuser:root /app

USER appuser

ENTRYPOINT ["/usr/bin/tini", "--", "/app/gongkan-worker"]
```

关键点：

```text
1. Runtime 基于 python:3.11-slim，而不是 alpine。
2. Go Worker 静态编译后复制进 Python runtime。
3. Python Core 使用 wheel 安装，不使用 editable install。
4. /app/python-core/config/docker.yaml 必须存在。
5. GONGKAN_PYTHON_EXECUTABLE 指向 /usr/local/bin/python。
6. GONGKAN_PYTHON_PROJECT_DIR 指向 /app/python-core。
7. 使用 tini 作为 entrypoint，避免子进程信号处理异常。
8. 使用非 root 用户运行。
```

---

### 5.3 新增 `config/docker.yaml`

目标：给 Worker 容器内的 Python Core 使用 Docker 网络下的配置。

建议从 `config/local.example.yaml` 复制并修改以下关键项：

```yaml
paths:
  project_root: /app/python-core
  data_dir: data
  artifacts_dir: artifacts
  qdrant_path: artifacts/15_vector_store/qdrant
  temp_dir: tmp

services:
  embedding_endpoint: http://111.19.156.74:8001/v1/embeddings
  embedding_model: qwen3-embedding-8b
  rerank_endpoint: http://111.19.156.74:8002/rerank
  rerank_model: ""
  chat_endpoint: http://111.19.156.30:8006/v1/chat/completions
  chat_model: deepseek-v4-flash
  chat_api_key_env: DEEPSEEK_API_KEY
  timeout_seconds: 120

qdrant:
  collection_name: datacenter_chunks_v1
  url: "http://qdrant:6333"
  api_key_env: QDRANT_API_KEY
  prefer_grpc: false
  timeout: 60
```

然后保留原本的 retrieval、grounding、evaluation、excel、agent 配置段。

注意：

```text
1. 容器内访问 Qdrant 必须用 http://qdrant:6333，不能用 localhost。
2. chat/embedding/rerank 如果在外部服务器，可继续使用内网 IP。
3. DEEPSEEK_API_KEY 只放到 .env.prod，不写进 YAML。
4. QDRANT_API_KEY 如果为空，.env.prod 里可以设为空字符串。
```

---

### 5.4 新增 `go-server/configs/config.docker.yaml`

目标：给 API 和 Worker 在 Docker Compose 网络中使用。

建议内容基于 `config.example.yaml`，但修改以下关键项：

```yaml
server:
  addr: ":8080"
  read_timeout: 10s
  write_timeout: 30s
  idle_timeout: 60s
  shutdown_timeout: 15s

database:
  dsn: "postgres://gongkan:gongkan@postgres:5432/gongkan?sslmode=disable"
  max_open_conns: 20
  max_idle_conns: 10
  max_conn_lifetime: 1h

redis:
  addr: "redis:6379"
  password: ""
  db: 0

storage:
  type: "minio"
  local_dir: "./runtime/storage"
  minio:
    endpoint: "minio:9000"
    access_key: "minioadmin"
    secret_key: "minioadmin"
    bucket: "gongkan-platform"
    use_ssl: false

python:
  executable: "/usr/local/bin/python"
  project_dir: "/app/python-core"
  config_path: "config/docker.yaml"
  default_timeout: "2h"
  artifact_validation_enabled: true
  kill_grace_period: "10s"
  stdout_log_max_bytes: 1048576
  stderr_log_max_bytes: 1048576
  step15_default_retrieval_mode: "layered"
  step15_default_prompt_version: "step15_compat"
  step15_default_rows: "4-144"
  ingest_command_enabled: true

jobs:
  fill_concurrency: 1
  ingestion_concurrency: 1
  max_python_processes: 1
  redis_namespace: "gongkan"
  worker_concurrency: 1
  default_timeout: "2h"
  max_attempts: 3
  retry_backoff: "30s"
  heartbeat_interval: "10s"
  event_buffer_size: 256
  enable_noop_job: false
  event_bus_enabled: true
  event_channel: "gongkan:run_events"

logging:
  level: "info"
  encoding: "json"
  development: false

observability:
  metrics_enabled: true
  pprof_enabled: false
  pprof_addr: "127.0.0.1:6060"
  tracing_enabled: false
  tracing_service_name: "gongkan-platform"
  tracing_exporter: "none"
  otlp_endpoint: ""
  log_request_body: false
  log_response_body: false

security:
  security_headers_enabled: true
  rate_limit_enabled: true
  rate_limit_rps: 20
  rate_limit_burst: 40
  body_limit_enabled: true
  max_body_size: "256MB"
  trusted_proxies: []
  cors_allow_credentials: false
  hsts_enabled: false
  hsts_max_age: "720h"

operations:
  graceful_shutdown_timeout: "30s"
  diagnostics_enabled: true
  expose_build_info: true

model_gateway:
  enabled: false
  bind_to_api: true
  internal_base_url: "http://api:8080/internal/model-gateway"
  require_internal_token: true
  internal_token_env: "NDR_MODEL_GATEWAY_TOKEN"
```

其余 `files`、`artifacts`、`auth`、`cors`、`model_gateway.defaults/chat/embedding/rerank/redis_limiter` 段可以从 `config.example.yaml` 保留。

注意：Go 配置支持环境变量覆盖。生产密码不要依赖 YAML 硬编码，应在 `.env.prod` 中用：

```text
GONGKAN_DATABASE_DSN
GONGKAN_MINIO_ACCESS_KEY
GONGKAN_MINIO_SECRET_KEY
GONGKAN_JWT_SECRET
GONGKAN_BOOTSTRAP_ADMIN_PASSWORD
```

---

### 5.5 新增 `go-server/deployments/.env.prod.example`

目标：提供生产环境变量模板，不提交真实 `.env.prod`。

建议内容：

```bash
# Database
POSTGRES_PASSWORD=change-this-postgres-password
GONGKAN_DATABASE_DSN=postgres://gongkan:change-this-postgres-password@postgres:5432/gongkan?sslmode=disable

# Redis
GONGKAN_REDIS_ADDR=redis:6379
GONGKAN_REDIS_PASSWORD=

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=change-this-minio-password
GONGKAN_MINIO_ENDPOINT=minio:9000
GONGKAN_MINIO_ACCESS_KEY=minioadmin
GONGKAN_MINIO_SECRET_KEY=change-this-minio-password
GONGKAN_MINIO_BUCKET=gongkan-platform

# Auth
GONGKAN_JWT_SECRET=change-this-to-a-long-random-secret-at-least-32-chars
GONGKAN_BOOTSTRAP_ADMIN_PASSWORD=change-this-admin-password
GONGKAN_BOOTSTRAP_ADMIN_PASSWORD_ENV=GONGKAN_BOOTSTRAP_ADMIN_PASSWORD

# Python Core in worker container
GONGKAN_PYTHON_EXECUTABLE=/usr/local/bin/python
GONGKAN_PYTHON_PROJECT_DIR=/app/python-core
GONGKAN_PYTHON_CONFIG_PATH=config/docker.yaml

# Small-user deployment: keep serial execution first
GONGKAN_JOBS_WORKER_CONCURRENCY=1
GONGKAN_JOBS_MAX_PYTHON_PROCESSES=1
GONGKAN_JOBS_FILL_CONCURRENCY=1
GONGKAN_JOBS_INGESTION_CONCURRENCY=1

# Model provider secrets
DEEPSEEK_API_KEY=replace-with-provider-key
QDRANT_API_KEY=

# Optional model gateway, disabled in this Docker baseline
GONGKAN_MODEL_GATEWAY_ENABLED=false
NDR_MODEL_GATEWAY_TOKEN=change-this-internal-token-if-gateway-enabled
```

`.gitignore` 已经忽略 `.env.*`，但仍需确认 `.env.prod` 不会被提交。

---

### 5.6 新增 `go-server/deployments/docker-compose.prod.yaml`

目标：提供单服务器长期运行的 Compose 文件。

建议内容：

```yaml
services:
  api:
    build:
      context: ..
      dockerfile: deployments/Dockerfile.api
    env_file:
      - .env.prod
    command: ["--config", "configs/config.docker.yaml"]
    restart: unless-stopped
    ports:
      - "8080:8080"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_started
      qdrant:
        condition: service_started
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8080/healthz"]
      interval: 10s
      timeout: 5s
      retries: 5
    volumes:
      - api_runtime:/app/runtime
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  worker:
    build:
      context: ../..
      dockerfile: go-server/deployments/Dockerfile.worker
    env_file:
      - .env.prod
    command: ["--config", "configs/config.docker.yaml"]
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_started
      qdrant:
        condition: service_started
    healthcheck:
      test: ["CMD-SHELL", "python -c 'import nested_doc_rag' && test -x /app/gongkan-worker"]
      interval: 30s
      timeout: 10s
      retries: 3
    volumes:
      - worker_runtime:/app/runtime
      - worker_python_artifacts:/app/python-core/artifacts
      - worker_python_tmp:/app/python-core/tmp
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: gongkan
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}
      POSTGRES_DB: gongkan
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U gongkan -d gongkan"]
      interval: 10s
      timeout: 5s
      retries: 5
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  redis:
    image: redis:7
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  minio:
    image: minio/minio:latest
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}
    volumes:
      - minio_data:/data
    ports:
      - "127.0.0.1:9001:9001"
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  minio-init:
    image: minio/mc:latest
    depends_on:
      minio:
        condition: service_started
    env_file:
      - .env.prod
    entrypoint: >
      /bin/sh -c "
      until mc alias set local http://minio:9000 $$MINIO_ROOT_USER $$MINIO_ROOT_PASSWORD; do sleep 2; done;
      mc mb -p local/$${GONGKAN_MINIO_BUCKET:-gongkan-platform} || true;
      mc anonymous set none local/$${GONGKAN_MINIO_BUCKET:-gongkan-platform} || true;
      "
    restart: "no"

  qdrant:
    image: qdrant/qdrant:latest
    restart: unless-stopped
    volumes:
      - qdrant_data:/qdrant/storage
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

volumes:
  postgres_data:
  redis_data:
  minio_data:
  qdrant_data:
  api_runtime:
  worker_runtime:
  worker_python_artifacts:
  worker_python_tmp:
```

注意：

```text
1. Postgres、Redis、Qdrant 不暴露公网端口。
2. API 暴露 8080。
3. MinIO console 只绑定 127.0.0.1:9001，可通过 SSH tunnel 访问。
4. Redis 开启 AOF 持久化。
5. 所有核心服务 restart: unless-stopped。
6. 所有核心服务配置 Docker 日志滚动。
7. Worker 的 build context 是仓库根目录 ../..。
```

---

### 5.7 修改 `go-server/Makefile`

新增生产 Compose targets，保留原有开发 targets。

建议新增：

```makefile
COMPOSE_PROD ?= docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod

.PHONY: docker-prod-config docker-prod-up docker-prod-down docker-prod-logs docker-prod-worker-smoke

docker-prod-config:
	$(COMPOSE_PROD) config

docker-prod-up:
	$(COMPOSE_PROD) up -d --build

docker-prod-down:
	$(COMPOSE_PROD) down

docker-prod-logs:
	$(COMPOSE_PROD) logs -f api worker

docker-prod-worker-smoke:
	$(COMPOSE_PROD) exec worker python --version
	$(COMPOSE_PROD) exec worker python -c "import nested_doc_rag; print('python core ok')"
	$(COMPOSE_PROD) exec worker sh -lc "cd /app/python-core && python -m nested_doc_rag.cli --help >/tmp/nested_doc_rag_help.txt && head -n 5 /tmp/nested_doc_rag_help.txt"
	$(COMPOSE_PROD) exec worker sh -lc "cd /app/python-core && python -m nested_doc_rag.cli show-config --config config/docker.yaml --json >/tmp/nested_doc_rag_config.json && head -n 20 /tmp/nested_doc_rag_config.json"
```

---

### 5.8 新增文档 `go-server/docs/DOCKER_PRODUCTION.md`

内容至少包含：

```text
1. 分支目标。
2. 生产启动前准备。
3. .env.prod 创建和修改。
4. docker compose 启动命令。
5. health check。
6. worker smoke test。
7. 日志查看。
8. 重启和停止。
9. 备份 volume 的建议。
10. 常见错误排查。
```

建议示例：

```bash
cd go-server
cp deployments/.env.prod.example deployments/.env.prod
vim deployments/.env.prod
make docker-prod-config
make docker-prod-up
make docker-prod-worker-smoke
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

停止但保留数据：

```bash
make docker-prod-down
```

查看日志：

```bash
make docker-prod-logs
```

不要在生产环境随意执行：

```bash
docker compose -f deployments/docker-compose.prod.yaml down -v
```

因为 `-v` 会删除 Postgres、Redis、MinIO、Qdrant 的持久化数据。

---

## 6. 并发策略

因为当前面向用户数量较少，本次生产 Docker baseline 保持串行执行：

```yaml
jobs:
  fill_concurrency: 1
  ingestion_concurrency: 1
  max_python_processes: 1
  worker_concurrency: 1
```

这意味着：

```text
1. 同一时间只有一个 Python 业务任务运行。
2. ingestion 和 fill 不会同时抢 Python、Qdrant、模型服务。
3. 日志更容易排查。
4. 系统吞吐较低，但稳定性更高。
```

后续如果任务等待时间明显过长，可以再升级为：

```yaml
jobs:
  fill_concurrency: 1
  ingestion_concurrency: 1
  max_python_processes: 2
  worker_concurrency: 2
```

但本分支不做复杂调度。

---

## 7. 验收标准

### 7.1 静态检查

```bash
git diff --check
cd go-server
go test ./...
go vet ./...
docker compose -f deployments/docker-compose.prod.yaml --env-file deployments/.env.prod config
```

### 7.2 镜像构建

从仓库根目录：

```bash
docker build -f go-server/deployments/Dockerfile.worker -t nested-doc-rag-worker:local .
```

从 `go-server` 目录：

```bash
make docker-prod-config
make docker-prod-up
```

### 7.3 Worker 容器检查

```bash
make docker-prod-worker-smoke
```

至少应通过：

```bash
python --version
python -c "import nested_doc_rag"
python -m nested_doc_rag.cli --help
python -m nested_doc_rag.cli show-config --config config/docker.yaml --json
```

### 7.4 服务健康检查

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/metrics
```

### 7.5 数据持久化检查

```text
1. 启动 compose。
2. 登录或执行一次 bootstrap admin。
3. 上传一个文件或创建一条业务数据。
4. docker compose restart。
5. 数据仍然存在。
6. docker compose down 后再 up，数据仍然存在。
```

### 7.6 真实业务 smoke

如果测试数据可用，必须跑完整链路：

```text
1. 上传知识库文档。
2. 创建 ingestion run。
3. ingestion 成功。
4. 上传表单。
5. 创建 fill run。
6. Worker 执行 Python Step15。
7. 下载 filled_form.xlsx。
8. 下载 review_items.csv。
```

---

## 8. 常见失败与排查

### 8.1 `exec: "python": executable file not found`

说明 Worker runtime 镜像不是 Python runtime，或 `GONGKAN_PYTHON_EXECUTABLE` 配错。

检查：

```bash
docker compose -f deployments/docker-compose.prod.yaml exec worker which python
```

预期：

```text
/usr/local/bin/python
```

---

### 8.2 `ModuleNotFoundError: No module named 'nested_doc_rag'`

说明 Python wheel 没安装成功，或 Docker build context 没有包含 `src/`。

检查：

```bash
docker compose -f deployments/docker-compose.prod.yaml exec worker python -c "import nested_doc_rag; print(nested_doc_rag.__file__)"
```

---

### 8.3 `config/docker.yaml not found`

说明 Dockerfile 没有复制 `config/docker.yaml` 到 `/app/python-core/config/docker.yaml`，或 Go config 中 `python.config_path` 配错。

检查：

```bash
docker compose -f deployments/docker-compose.prod.yaml exec worker ls -l /app/python-core/config
```

---

### 8.4 Python 内访问 Qdrant 失败

如果配置为：

```yaml
qdrant:
  url: "http://localhost:6333"
```

在 Worker 容器内是错的。应改为：

```yaml
qdrant:
  url: "http://qdrant:6333"
```

---

### 8.5 Postgres / Redis / MinIO 连接失败

确认 Go 生产配置使用 Docker service name：

```text
postgres:5432
redis:6379
minio:9000
qdrant:6333
```

不要在容器内使用 `localhost` 访问其他容器。

---

## 9. 最终交付物

本分支最终至少包含以下文件变更：

```text
新增：
- .dockerignore
- config/docker.yaml
- go-server/configs/config.docker.yaml
- go-server/deployments/.env.prod.example
- go-server/deployments/docker-compose.prod.yaml
- go-server/docs/DOCKER_PRODUCTION.md

修改：
- go-server/deployments/Dockerfile.worker
- go-server/Makefile

可选修改：
- go-server/README.md，增加 Docker production doc 链接
```

---

## 10. 合并前 checklist

```text
[ ] Worker Dockerfile 使用仓库根目录作为 build context。
[ ] Worker 镜像包含 Python 3.11。
[ ] Worker 镜像安装 nested_doc_rag wheel。
[ ] Worker 镜像包含 /app/python-core/config/docker.yaml。
[ ] Go config 中 python.executable=/usr/local/bin/python。
[ ] Go config 中 python.project_dir=/app/python-core。
[ ] Go config 中 python.config_path=config/docker.yaml。
[ ] Python config 中 qdrant.url=http://qdrant:6333。
[ ] docker-compose.prod.yaml 中 Postgres、Redis、MinIO、Qdrant 有 volume。
[ ] Redis 开启 AOF。
[ ] Postgres、Redis、Qdrant 不暴露公网端口。
[ ] .env.prod 不会被提交。
[ ] make docker-prod-worker-smoke 通过。
[ ] go test ./... 通过。
[ ] docker compose config 通过。
```
