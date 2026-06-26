# nested-doc-rag Docker 完整化与回填增强设计摘要

## 1. 目标定位

当前 `ops/docker-worker-python-core` 分支已经具备小团队单机生产部署的基本骨架。系统由 Go API、Go Worker、Python Core、Postgres、Redis、MinIO、Qdrant 组成。Go 侧负责登录鉴权、任务编排、文件归档、任务进度、artifact 下载与审计。Python Core 负责知识入库、检索、Step15/Agent 执行、Excel 回写与运行产物生成。

下一阶段不应继续扩大架构边界。核心目标只有两件事。

1. Docker 部署完整化  
   让项目可以打包成可迁移、可复现、可升级、可回滚的单机 Docker 交付包。远端服务器不应依赖源码构建，也不应依赖宿主机 Python 环境。

2. 回填体系增强  
   不只回填高置信字段。对于“有证据但不确定”的字段也要回填，但必须标红、写明证据来源，并在存在图片证据时把图片证据一并带到结果文件和 artifact 中。当前项目未做图片 OCR，所以图片只作为文字证据的佐证，不作为独立识别来源。

---

## 2. 当前状态判断

### 2.1 已具备的基础

当前分支已经具备以下工程基础。

- `Dockerfile.api` 已能构建 Go API 运行镜像。
- `Dockerfile.worker` 已将 Go Worker 与 Python Core wheel 打入同一运行镜像。
- `docker-compose.prod.yaml` 已包含 API、Worker、Postgres、Redis、MinIO、Qdrant。
- Worker 通过 subprocess 调用 Python CLI，边界清楚。
- Python 产出 `run_manifest.json`、`filled_form.xlsx`、`review_items` 等 artifact。
- Go 侧通过 manifest 归档 artifact，并向前端提供下载。
- 当前已有 `healthz`、`readyz`、`metrics`、runbook 与生产 Docker 文档雏形。
- 当前检索 payload 中已有 `source_anchor`、`source_type`、`proof_attachment_ids` 等证据关联字段，可作为增强回填的基础。

### 2.2 主要缺口

当前缺口不是“大架构不够”，而是交付闭环不足。

- 生产 compose 仍偏源码构建，没有形成稳定镜像 tag 和 registry 交付。
- dev compose 中 worker build context 存在路径不一致风险。
- MinIO、Qdrant 使用 `latest` 风险较高，应锁定版本。
- MinIO、Qdrant readiness healthcheck 不完整，`depends_on` 不能保证服务可用。
- 缺少统一的 `preflight-prod`、`backup-prod`、`restore-prod`、`upgrade-prod`、`rollback-prod` 命令。
- 缺少 HTTPS 入口与静态前端托管模板。
- CI 主要覆盖 Go，不足以保证 Docker + Python + Compose 可运行。
- 当前回填偏保守，不确定字段进入 review，但用户希望“有证据的不确定字段也写入，只是显著标记”。

---

## 3. Docker 完整化设计

### 3.1 设计原则

保持单机 Docker Compose，不引入 Kubernetes、Kafka、复杂多租户或大型 DevOps 系统。小团队交付的关键不是弹性伸缩，而是可迁移、可恢复、可排障。

目标交付形态如下。

```text
一台 Linux 服务器
Docker + Docker Compose
.env.prod
docker-compose.prod.yaml
可选 docker-compose.edge.yaml
registry 中的 api/worker 镜像
持久化 volume
统一 Makefile 运维命令
```

### 3.2 Compose 与镜像

需要保留本地 build 能力，同时支持远端 image pull。

建议在 `docker-compose.prod.yaml` 中为 API 与 Worker 增加 image 变量。

```yaml
api:
  image: ${REGISTRY}/${IMAGE_NS}/gongkan-api:${IMAGE_TAG}
  build:
    context: ..
    dockerfile: deployments/Dockerfile.api

worker:
  image: ${REGISTRY}/${IMAGE_NS}/gongkan-worker:${IMAGE_TAG}
  build:
    context: ../..
    dockerfile: go-server/deployments/Dockerfile.worker
```

这样本地仍可执行 build，远端可只执行 pull 与 up。

同时修复 dev compose 中 Worker build context 路径，确保 `Dockerfile.worker` 能访问仓库根目录、`go-server/go.mod` 与 Python `pyproject.toml`。

### 3.3 版本锁定与健康检查

生产 compose 不应使用 `latest`。MinIO、Qdrant、Postgres、Redis 均应锁定明确版本，并集中写在 `.env.prod.example`。

MinIO 与 Qdrant 要增加 healthcheck。

```yaml
minio:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/ready"]
    interval: 10s
    timeout: 5s
    retries: 12

qdrant:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:6333/readyz"]
    interval: 10s
    timeout: 5s
    retries: 12
```

API 与 Worker 对 MinIO、Qdrant 的依赖应从 `service_started` 改为 `service_healthy`。

### 3.4 运维脚本

新增脚本目录。

```text
go-server/scripts/preflight_prod.sh
go-server/scripts/build_release.sh
go-server/scripts/push_release.sh
go-server/scripts/backup_prod.sh
go-server/scripts/restore_prod.sh
go-server/scripts/upgrade_prod.sh
go-server/scripts/rollback_prod.sh
```

Makefile 对外暴露固定命令。

```bash
make preflight-prod
make docker-build REGISTRY=... IMAGE_NS=... IMAGE_TAG=...
make docker-push REGISTRY=... IMAGE_NS=... IMAGE_TAG=...
make backup-prod BACKUP_DIR=...
make restore-prod BACKUP_DIR=...
make upgrade-prod IMAGE_TAG=...
make rollback-prod IMAGE_TAG=... BACKUP_DIR=...
```

`preflight-prod` 至少检查：

- Docker 与 Compose 版本
- `.env.prod` 必填项
- API/Worker image tag
- `docker compose config` 是否通过
- Postgres、Redis、MinIO、Qdrant 配置是否存在
- MinIO bucket 是否可初始化
- Python Core smoke test 是否通过
- 服务器磁盘空间
- 必要端口占用
- 模型服务 endpoint 是否可连通

`backup-prod` 至少覆盖：

- Postgres `pg_dump`
- MinIO volume 或 bucket 对象
- Qdrant volume
- Redis volume
- API/Worker runtime artifact volume
- 当前 `.env.prod` 与 compose 文件快照

`restore-prod` 必须能从同一 backup 目录恢复以上状态。

### 3.5 HTTPS 与前端入口

新增 `docker-compose.edge.yaml` 与 `Caddyfile`。Caddy 用于：

- HTTPS 终止
- 静态前端分发
- `/api/*` 反代到 `api:8080`
- SSE 长连接支持
- 上传大小限制
- 基本安全 header

示例：

```caddy
${DOMAIN} {
  encode gzip
  root * /srv/web
  file_server

  handle_path /api/* {
    reverse_proxy api:8080
  }

  handle /healthz {
    reverse_proxy api:8080
  }

  handle /readyz {
    reverse_proxy api:8080
  }
}
```

### 3.6 CI/CD

新增或扩展 GitHub Actions。

PR 阶段：

- Go format/test/vet/build
- Python pytest
- API image build
- Worker image build
- docker compose config 校验
- Worker smoke test

Tag 发布阶段：

- 登录 registry
- 构建 `gongkan-api`
- 构建 `gongkan-worker`
- 推送 semver tag、git sha tag
- 生成 SBOM/provenance 可选

---

## 4. 回填增强设计

### 4.1 核心原则

增强回填不是让模型“更大胆地瞎填”，而是把答案状态显式写进 Excel 与 artifact。

字段分为三类。

```text
confirmed   高置信，正常回填
uncertain   有证据但不确定，回填但红色标记，并写入证据说明
flagged     不可写入，只进入 review_items
```

### 4.2 Excel 回写规则

| 状态 | 是否写入 Excel | 样式 | 说明 |
|---|---|---|---|
| confirmed | 写入 | 默认样式，可附 comment | 高置信结果 |
| uncertain | 配置允许且目标单元格为空时写入 | 红色字体或红色填充，必须附 comment | 有证据但需人工复核 |
| flagged | 不写入 | 无 | 无可写答案、证据缺失、冲突严重、定位失败或策略拒绝 |

配置项建议：

```yaml
writeback:
  allow_uncertain: true
  uncertain_style: red_fill
  uncertain_comment_prefix: "[UNCERTAIN]"
  embed_evidence_images: false
  evidence_image_mode: hyperlink
  max_evidence_images_per_field: 3
  max_comment_chars: 2000
```

默认行为应保持保守。如果配置缺省，维持当前只写 confirmed 的行为。用户明确打开后再写 uncertain。

### 4.3 证据引用结构

扩展 `run_manifest.json` 到 `schema_version=1.1`。每个字段写入 `evidence_refs`。

```json
{
  "schema_version": "1.1",
  "run_id": "fill_20260622_001",
  "writeback": {
    "summary": {
      "confirmed": 82,
      "uncertain": 11,
      "flagged": 6,
      "written": 89
    },
    "fields": [
      {
        "field_key": "row_25_power_supply",
        "sheet_name": "基地云机房",
        "cell": "D25",
        "status": "uncertain",
        "answer_status": "partial_clue",
        "answer_value": "双路市电",
        "writeback_action": "written_red_comment",
        "evidence_refs": [
          {
            "document_id": "doc_123",
            "object_key": "kb/xixian_4/docs/capability.xlsx",
            "object_version_id": "optional",
            "qdrant_point_id": "pt_987",
            "source_type": "main_excel_capability",
            "source_anchor": "能力清单!H42",
            "page": null,
            "sheet_name": "能力清单",
            "cell": "H42",
            "image_object_key": "runs/fill_001/evidence/img_002.png",
            "bbox": [12, 88, 640, 420],
            "caption": "配电系统图片佐证"
          }
        ]
      }
    ]
  }
}
```

### 4.4 图片证据处理

当前项目未做图片 OCR，所以图片不能作为独立文本来源。它的角色是“对命中文字证据的佐证”。

处理规则：

1. 检索命中的 chunk 如果 payload 中包含 `proof_attachment_ids`，则把对应图片加入该字段的 `evidence_refs`。
2. 原图或裁剪图上传到 MinIO。
3. manifest 中只保存 `image_object_key`、可选 `object_version_id`、`bbox`、`caption`。
4. Excel 中默认写图片证据链接，不默认嵌入图片，避免文件膨胀。
5. 如果 `WRITEBACK_EMBED_IMAGE=true`，则可在单独的 evidence sheet 中插入缩略图，并在字段 comment 中引用 evidence sheet 位置。
6. 不把图片二进制写入数据库，不把图片放进 Qdrant 向量。

推荐 Excel 结果形态：

- 主表原字段处写值。
- uncertain 字段红色标记。
- comment 中写证据摘要、原始文档、位置、图片证据链接。
- 新增 `Evidence` sheet，列出字段、答案、证据文本、原始位置、图片链接，可选缩略图。

### 4.5 Go 后端与 artifact 导入

Go Worker 导入 manifest 1.1 时需要：

- 解析 `status`
- 解析 `writeback_action`
- 解析 `evidence_refs`
- 将 uncertain/flagged 同步到 review_items 或扩展表
- 保存 `writeback_audit.jsonl`
- 对图片证据 object key 做权限校验与下载授权
- 保证 `filled_form.xlsx`、`review_items`、`writeback_audit`、`run_manifest` 之间的 field key 一致

前端至少展示：

- confirmed 数量
- uncertain 数量
- flagged 数量
- 写入字段数量
- 需要人工复核字段数量
- 每个 uncertain 字段的证据来源与图片链接
- `filled_form.xlsx`、`review_items.csv/jsonl`、`writeback_audit.jsonl` 下载入口

### 4.6 校验与错误码

Python `validate-artifacts` 应校验：

- manifest schema 合法
- `status` 与 `writeback_action` 枚举合法
- uncertain 必须至少有一条 `evidence_ref`
- `sheet_name/cell` 合法
- evidence object key 存在
- image object key 存在
- comment 长度不超限
- 写回统计与字段列表一致

统一错误码：

```text
WB_INVALID_CELL
WB_MISSING_EVIDENCE
WB_OBJECT_NOT_FOUND
WB_IMAGE_UPLOAD_FAILED
WB_EMBED_IMAGE_FAILED
WB_COMMENT_TOO_LONG
WB_POLICY_REJECTED
WB_MANIFEST_SCHEMA_INVALID
```

这些错误码进入 `writeback_audit.jsonl`，必要时也进入 review_items。

---

## 5. 实施优先级

### P0 Docker 交付闭环

- 修复 worker build context
- prod compose 增加 image tag
- 锁定依赖镜像版本
- 增加 MinIO/Qdrant healthcheck
- 增加 preflight、backup、restore、upgrade、rollback
- 增加 CI Docker build 与 smoke test

### P1 回填增强基础

- manifest 1.1
- 三态字段模型
- uncertain 红色回填与 comment
- evidence_refs 结构
- writeback_audit.jsonl
- Go 导入 manifest 1.1
- review_items 扩展 evidence 字段

### P2 图片证据与前端增强

- proof_attachment_ids 到 image_object_key 的映射
- 图片上传 MinIO
- Excel Evidence sheet
- 前端展示 uncertain evidence 与图片链接
- artifact retention 策略

---

## 6. 验收标准

Docker 侧：

- 远端服务器无需源码即可从 registry 拉起 API/Worker。
- `make preflight-prod` 能在部署前发现配置问题。
- `make backup-prod` 与 `make restore-prod` 可恢复完整运行状态。
- `make upgrade-prod` 与 `make rollback-prod` 可切换镜像版本。
- API、Worker、Postgres、Redis、MinIO、Qdrant 均有健康检查。
- CI 能覆盖 Go、Python、Docker build 与 compose smoke。

回填侧：

- confirmed 字段正常写入。
- uncertain 字段在配置允许时写入，并红色标记。
- uncertain 字段 comment 中包含原始文档和位置。
- 含图片证据的字段能在 artifact 中看到图片链接或 Evidence sheet。
- flagged 字段不写入 Excel，只进入 review_items。
- manifest、review_items、writeback_audit 中 field key 一致。
- 缺证据的 uncertain 会被 validator 拒绝。
