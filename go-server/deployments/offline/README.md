# 离线部署

每份包对应一种 Linux 架构，包含 API、Worker、带静态 UI 的 Caddy、PostgreSQL、Redis、MinIO、Qdrant 共七个镜像。服务器不需要 npm、Go、Python、镜像仓库或源代码。需要已运行的 Docker、支持 `up --wait` / `--pull never` 的 Docker Compose v2、Bash，以及 `sha256sum` 或 `shasum`。

在构建机从项目根目录打包：

```sh
PYTHONPATH=src python scripts/build_vnext_offline_bundle.py \
  --architecture amd64 --models-env /path/private.env
```

ARM64 服务器使用 `--architecture arm64` 的独立包。上传并解压对应的包，在包目录运行：

```sh
./start.sh
```

脚本先核对 `SHA256SUMS`、`bundle.json` 和宿主/ Docker daemon 架构，再导入 `images/all-images.tar`。加载后检查七个镜像的平台、RootFS 层指纹以及三个应用镜像的提交标签。构建机和服务器的 Docker 镜像存储实现可能使 `.Id` 表示不同类型的 digest，因此构建/服务器 ID 和 config digest 记录在 `runtime-logs/loaded-images.log`，文件 SHA256 和同序 RootFS 指纹用于跨实现核对。

首次启动生成随机数据库、MinIO、JWT 和 bootstrap admin 密码到本机 `.env`，权限为 `600`；默认用户名为 `admin`。脚本不输出密码或模型 key。请在服务器上私下读取并保管本机 `.env`。再次启动保留该文件和数据卷，密码不会重置。不要将 `.env.example` 覆盖到已有 `.env`；不要删除 `.env` 后重新生成数据库密码。

默认只有 HTTP 端口 `8003` 对外开放，访问 `http://服务器IP:8003`。API 和四个基础服务均只在本项目 Docker 网络内可访问。UI 静态资源和 Caddyfile 内嵌于 Web 镜像，不挂载构建机或旧工作区文件。更改端口、监听地址或项目名时修改本机 `.env`；已初始化项目不能靠改名迁移数据卷。

`models.env` 可由打包器按 allowlist 携带，也可在服务器配置，权限应为 `600`。常用配置是 `NDR_CHAT_ENDPOINT` / `NDR_CHAT_MODEL`、`NDR_EMBEDDING_ENDPOINT` / `NDR_EMBEDDING_MODEL`、`NDR_RERANK_ENDPOINT` / `NDR_RERANK_MODEL`、`CHAT_API_KEY_ENV=DEEPSEEK_API_KEY` 及该变量的实际 key。端点必须能从 Worker 容器访问；服务器上的服务可使用 `host.docker.internal`。没有模型配置时 UI 和基础服务仍可启动，上传入库和填表的模型请求需要有效的三个模型接口。此包不包含模型权重。

默认 `GONGKAN_MODEL_GATEWAY_ENABLED=false`，Python 直接使用上述 endpoint overrides。若启用 Go model gateway，还需为 API 配置 `GONGKAN_MODEL_GATEWAY_CHAT_UPSTREAM_URL`、`GONGKAN_MODEL_GATEWAY_EMBEDDING_UPSTREAM_URL`、`GONGKAN_MODEL_GATEWAY_RERANK_UPSTREAM_URL` 以及其内部 token；只修改 `NDR_*` 不会修改 Go gateway 的 upstream。运行时 env 文件由 Compose 解析，脚本不会 source 或 eval 它们。

若 `models.env` 配置非空 `QDRANT_API_KEY`，脚本在本机私有 `.qdrant.env` 中为 Qdrant 设置同一 `QDRANT__SERVICE__API_KEY`；默认不启用该 key。不要只改服务端或 Worker 一侧。

HTTP 默认 `DOMAIN=:80`。已有 HTTPS 反向代理可转发到此 HTTP 入口；需要关闭 buffering，以支持 SSE，并允许 2GB 上传和长任务请求。该模板不自动申请证书，不依赖离线服务器访问 ACME，也不对外发布额外的 443 端口。

## 全新数据库与已有数据库

默认使用独立项目 `datacenter-vnext`，没有 knowledge-seed 服务或历史知识/索引挂载。API 自动建 MinIO bucket、执行迁移 1–13 并创建 bootstrap admin，Worker 等待 API ready 后启动。迁移文件保持原字节不变。

冻结迁移 8/12 会插入历史知识元数据，即使实际对象和向量从未导入。仅当脚本在创建本项目 PostgreSQL 数据卷**之前**确认该卷不存在时，才持久记录 `.fresh-install-pending`。API ready 后、Web 对外开放前，`fresh-cleanup.sql` 在事务及迁移 advisory lock 下清除准确匹配的十个历史 KB/旧版本与十四份文件/文档元数据，保留默认 workspace、管理员、角色和迁移账本。SQL 要求账本为全新执行的 1–13，并拒绝任何用户 job、form、ingestion、artifact、review、pin、额外 workspace 或不匹配的 bootstrap 记录。断电重试保留 marker；已提交清理但尚未更新 marker 的空状态可安全重试。清理成功后写 `.initialized`，再启动 Web。

已有 PostgreSQL 数据卷且没有本脚本的 fresh marker 时，**绝不执行清理**，只使用新迁移引擎前进迁移、保留既有数据。要升级原部署，先备份数据库/对象/向量数据，停止使用同一数据的全部旧 API、Worker 和旧 knowledge-seed 容器，再使用原项目名、原凭据和原数据卷配置。脚本替换本项目镜像前也会停止该项目的 API、Worker 和遗留 seed；它不会停止其他项目的容器。旧二进制不识别迁移账本，会重放旧 seed，不能混跑或做滚动升级。不要使用旧 `upgrade_prod.sh` / `rollback_prod.sh`，它们会 pull 并可能混跑旧版本。

```sh
./status.sh
./logs.sh api worker
./stop.sh
```

`stop.sh` 只停止本项目服务，保留全部数据卷，不执行 `down -v`、镜像 prune 或全局 Docker 配置修改。首次失败的日志位于 `runtime-logs/`；先修正原因再运行 `start.sh`，不要删除 fresh marker 来绕过清理断言。
