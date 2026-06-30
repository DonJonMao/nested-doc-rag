# 写回37终版 Docker 部署手册

本文档面向当前 `writeback-37-final` 口径：Worker 容器内置 Python Core，生产默认使用 `config/docker.yaml`。该配置等价于“单 AnswerArbitration LLM Agent + 规则化 grounding/writeback gate”，不是 MAS 强门控版本。

## 1. 镜像内容

Worker 镜像包含：

- Go worker 二进制：`/app/gongkan-worker`
- Python 3.11
- 已安装的 `nested_doc_rag` wheel
- Go 配置：`/app/configs/config.docker.yaml`
- Python 配置：`/app/python-core/config/docker.yaml`
- 实验配置备份：`/app/python-core/config/experiments/reconstructed_relaxed_25.yaml`

API 镜像包含 Go API 二进制、Go 配置和数据库迁移文件。

本次本地交付镜像包按服务器 CPU 架构区分，默认生成在仓库根目录：

```text
artifacts/docker/gongkan-writeback37-linux-amd64-images.tar  # 常见 x86_64 服务器
artifacts/docker/gongkan-writeback37-linux-arm64-images.tar  # ARM64 服务器或 Apple Silicon 本地验证
```

两个包内的镜像 tag 都是：

```text
ghcr.io/donjonmao/nested-doc-rag/gongkan-api:writeback-37-final
ghcr.io/donjonmao/nested-doc-rag/gongkan-worker:writeback-37-final
```

## 2. 本地构建镜像

从仓库根目录进入 Go 服务目录：

```bash
cd go-server
cp deployments/.env.prod.example deployments/.env.prod
```

编辑 `deployments/.env.prod`，至少修改：

```bash
REGISTRY=ghcr.io
IMAGE_NS=donjonmao/nested-doc-rag
IMAGE_TAG=writeback-37-final

POSTGRES_PASSWORD=<强密码>
GONGKAN_DATABASE_DSN=postgres://gongkan:<强密码>@postgres:5432/gongkan?sslmode=disable

MINIO_ROOT_PASSWORD=<强密码>
GONGKAN_MINIO_SECRET_KEY=<同上或另一个强密码>
GONGKAN_JWT_SECRET=<至少32字符随机字符串>
GONGKAN_BOOTSTRAP_ADMIN_PASSWORD=<管理员初始密码>
DEEPSEEK_API_KEY=<模型服务API Key>
```

先检查 Compose 配置：

```bash
make docker-prod-config
```

构建 API 和 Worker 镜像：

```bash
IMAGE_TAG=writeback-37-final make docker-build
```

构建完成后应看到两个镜像：

```bash
docker images | grep gongkan
```

预期镜像名：

```text
ghcr.io/donjonmao/nested-doc-rag/gongkan-api:writeback-37-final
ghcr.io/donjonmao/nested-doc-rag/gongkan-worker:writeback-37-final
```

## 3. 推送镜像到镜像仓库

如果服务器从镜像仓库拉取镜像，先登录仓库：

```bash
docker login ghcr.io
```

推送：

```bash
IMAGE_TAG=writeback-37-final make docker-push
```

如果服务器无法访问镜像仓库，也可以导出镜像包：

```bash
docker save \
  ghcr.io/donjonmao/nested-doc-rag/gongkan-api:writeback-37-final \
  ghcr.io/donjonmao/nested-doc-rag/gongkan-worker:writeback-37-final \
  -o artifacts/docker/gongkan-writeback37-linux-<arch>-images.tar
```

服务器上导入：

```bash
docker load -i artifacts/docker/gongkan-writeback37-linux-amd64-images.tar
```

## 4. 服务器部署文件

服务器只需要这些文件：

```text
deployments/docker-compose.prod.yaml
deployments/.env.prod
```

如果不用镜像仓库，还需要把镜像包一起传到服务器：

```text
artifacts/docker/gongkan-writeback37-linux-amd64-images.tar
```

可选 HTTPS 静态站点：

```text
deployments/docker-compose.edge.yaml
deployments/Caddyfile
web/dist/
```

服务器目录建议：

```bash
mkdir -p /opt/gongkan/deployments
cd /opt/gongkan
```

把 compose 和 `.env.prod` 放入 `/opt/gongkan/deployments/`。

如果使用镜像包部署，可以放到：

```text
/opt/gongkan/artifacts/docker/gongkan-writeback37-linux-amd64-images.tar
```

然后在服务器上导入：

```bash
cd /opt/gongkan
docker load -i artifacts/docker/gongkan-writeback37-linux-amd64-images.tar
docker images | grep gongkan
```

## 5. 启动数据库和服务

首次启动：

```bash
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  up -d
```

该命令会启动：

- `postgres`
- `redis`
- `minio`
- `minio-init`
- `qdrant`
- `api`
- `worker`

Postgres/Redis/MinIO/Qdrant 数据默认保存在 Docker named volumes 中：

```text
postgres_data
redis_data
minio_data
qdrant_data
api_runtime
worker_runtime
worker_python_artifacts
worker_python_tmp
```

不要在生产环境执行带 `-v` 的 `docker compose down -v`，否则会删除数据卷。

## 6. 健康检查

检查容器状态：

```bash
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  ps
```

检查 API：

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

检查 Worker 内 Python Core：

```bash
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  exec worker python --version

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  exec worker /app/gongkan-worker --help

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  exec worker sh -lc 'cd /app/python-core && python -m nested_doc_rag.cli show-config --config config/docker.yaml --json | head -40'
```

确认输出中包含：

```json
"relaxed_writeback_gate_enabled": true
"field_binding_agent_enabled": false
"slot_decomposition_enabled": false
"pre_writeback_consistency_enabled": false
```

## 7. 知识库初始化

镜像不内置本地 `data/` 和 `artifacts/`，生产环境应通过平台上传材料并触发 ingestion。原因是 `.dockerignore` 排除了本地数据，避免把测试材料和大体积索引打进镜像。

初始化流程：

1. 登录 API 创建 workspace。
2. 上传知识库文件、调研表模板等材料。
3. 创建 ingestion job。
4. 等待 ingestion 成功，Qdrant 中生成 `datacenter_chunks_v1` collection/points。
5. 创建填表 run，选择 target namespace，例如 `xixian_4`，room context 例如 `西咸4号楼 301机房`。

## 8. 日志与排障

查看 API/Worker 日志：

```bash
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  logs -f api worker
```

常见问题：

- `ModuleNotFoundError: nested_doc_rag`：Worker 镜像没有正确构建 Python wheel，重新构建 worker。
- `config/docker.yaml not found`：Worker 镜像缺 Python config，确认使用当前 Dockerfile。
- Qdrant 连接失败：容器内必须使用 `http://qdrant:6333`，不要用 `localhost:6333`。
- 模型服务失败：确认服务器能访问 `config/docker.yaml` 里的 embedding/rerank/chat endpoint，且 `DEEPSEEK_API_KEY` 已设置。

## 9. 备份、升级和回滚

如果服务器只放了 compose/env 文件，生产操作建议直接使用 `docker compose`，不要依赖本地 Makefile。

升级前至少备份 Postgres 逻辑数据：

```bash
mkdir -p /var/backups/gongkan
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  exec -T postgres pg_dump -U gongkan -d gongkan \
  > /var/backups/gongkan/postgres-$(date +%Y%m%d_%H%M%S).sql
```

如果是镜像仓库部署，先修改 `deployments/.env.prod` 中的 `IMAGE_TAG`，然后拉取并重建 API/Worker：

```bash
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  pull api worker

docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  up -d api worker
```

如果是镜像包部署，先 `docker load -i <镜像包>`，再执行上面的 `up -d api worker`。

回滚到旧 tag 时，改回 `.env.prod` 里的 `IMAGE_TAG`，确保旧镜像已 pull 或 load，然后重建 API/Worker：

```bash
docker compose \
  --env-file deployments/.env.prod \
  -f deployments/docker-compose.prod.yaml \
  up -d api worker
```
