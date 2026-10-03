# 74 机器填表任务与镜像问题交接文档（2026-07-10）

本文档用于交接给新的 Codex 会话继续排查。内容分为“已验证事实”“当前推断”“本地已做修复”“仍需在 74 上确认的事项”。不要把推断当成事实；接管时优先用本文末尾的命令重新采集 74 机器实时状态。

## 1. 快速结论

1. 用户贴出的 run `4e01a7aa-c68c-41d5-8dd1-6c7c1065bb75` 显示“已取消”，不是前端误点取消。数据库中 `cancel_requested_at` 为空，任务在开始后正好 30 分钟被标记为 `canceled`。
2. 直接原因高度确定为 Asynq 任务 timeout：该 run 的 job 在 `2026-07-10 08:47:32 UTC` 开始，在 `2026-07-10 09:17:32 UTC` 结束，刚好 30 分钟；`09:17:22 UTC` 仍有 heartbeat。
3. 另一个已确认问题是前端进度长期不显示或显示 `0/141`：Step15 实际持续写 `trace.checkpoint.jsonl`，而 Go 侧进度 watcher 原先只看最终 `trace.jsonl`，导致 DB 的 `fill_runs.progress_done` 没有随 checkpoint 更新。
4. 本地已生成一个修复版包，但截至本文档编写时，不能确认该包已经部署到 74。需要接管者先确认 74 当前容器镜像 tag。
5. 旧 run 已经是终止态，且 Python 进程已消失。不能指望前端自动恢复旧 run；部署修复版后应新建 run，或明确设计手动 requeue/reset 流程。

## 2. 环境与路径

本地开发仓库：

```bash
/Users/mao/projects/datacenter
```

74 机器上用户当前使用的目录：

```bash
~/server_bundle_writeback37_livebase_4mode_qdrantfix_nojudge_arm64
```

74 机器上该 run 的 out dir：

```bash
/app/python-core/artifacts/runs/4e01a7aa-c68c-41d5-8dd1-6c7c1065bb75
```

用户贴出的容器名包括：

```bash
gongkan-worker-1
gongkan-postgres-1
```

API、前端、Redis、Qdrant、MinIO 等容器在用户输出中没有展示。接管时需要重新 `docker ps`。

## 3. 74 机器上旧 run 的已验证状态

用户在 74 机器上查询 `fill_runs` 得到：

```text
id             = 4e01a7aa-c68c-41d5-8dd1-6c7c1065bb75
status         = canceled
progress_done  = 0
progress_total = 141
error_message  =
job_id         = a7c63ddd-5eca-4257-ba04-e57d30f45fa5
out_dir        = /app/python-core/artifacts/runs/4e01a7aa-c68c-41d5-8dd1-6c7c1065bb75
created_at     = 2026-07-10 08:47:32.435427+00
queued_at      = 2026-07-10 08:47:32.446804+00
started_at     = 2026-07-10 08:47:32.6779+00
finished_at    = 2026-07-10 09:17:32.683344+00
updated_at     = 2026-07-10 09:17:32.68389+00
```

对应 `jobs` 状态：

```text
id                  = a7c63ddd-5eca-4257-ba04-e57d30f45fa5
status              = canceled
attempt             = 1
max_attempts        = 3
error_message       =
cancel_requested_at =
queued_at           = 2026-07-10 08:47:32.439365+00
started_at          = 2026-07-10 08:47:32.666268+00
heartbeat_at        = 2026-07-10 09:17:22.673485+00
finished_at         = 2026-07-10 09:17:32.684683+00
updated_at          = 2026-07-10 09:17:32.685004+00
```

最新 run events 中，sequence 183 是 heartbeat，sequence 184 是 canceled：

```text
183 heartbeat 2026-07-10 09:17:22.674174+00
184 canceled  2026-07-10 09:17:32.68536+00
```

这说明任务在被标记 canceled 前 worker 心跳正常。`cancel_requested_at` 为空，不能解释为用户主动取消。

## 4. 旧 run 的文件进展

用户在 `2026-07-10 10:18:02 UTC` 查询 out dir：

```text
run_state.json:
  fields_total     = 141
  fields_completed = 14
  fields_failed    = 0
  finished_at      = ""

checkpoint 行数:
  agent_overlays.checkpoint.jsonl = 14
  predictions.checkpoint.jsonl    = 14
  review_items.checkpoint.jsonl   = 12
  trace.checkpoint.jsonl          = 266
```

关键文件修改时间：

```text
predictions.checkpoint.jsonl Modify = 2026-07-10 09:17:07.247951693 +0000
trace.checkpoint.jsonl       Modify = 2026-07-10 09:17:07.263952660 +0000
```

这说明 Python 侧至少处理到了 14 个字段，但 DB 里 `progress_done` 仍是 0。这个现象与“Go 侧未读取 checkpoint trace 更新进度”一致。

## 5. 旧 run 的 Python 进程状态

用户在 `2026-07-10 09:14 UTC` 左右看到 worker 容器内 Python 进程仍在：

```bash
/usr/local/bin/python -m nested_doc_rag.cli run-step15-agent \
  --config config/docker.yaml \
  --target-namespace xixian_4 \
  --global-namespace global \
  --room-context 301机房 \
  --rows 4-144 \
  --retrieval-plan layered \
  --prompt-version step15_compat \
  --out-dir /app/python-core/artifacts/runs/4e01a7aa-c68c-41d5-8dd1-6c7c1065bb75 \
  --no-judge \
  --template /app/python-core/artifacts/runs/4e01a7aa-c68c-41d5-8dd1-6c7c1065bb75/input/基地云机房信息调研表.xlsx \
  --writeback \
  --resume
```

用户在 `2026-07-10 10:18 UTC` 再查：

```bash
sudo docker top gongkan-worker-1 | grep -E 'run-step15-agent|nested_doc_rag|python' || true
```

无输出。说明 Python 进程已经退出或被杀，旧 run 不再继续跑。

## 6. 已确认的镜像/版本问题

### 6.1 Asynq 30 分钟默认 timeout

症状：

```text
前端显示 run 已取消
DB: fill_runs.status = canceled
DB: jobs.status = canceled
cancel_requested_at 为空
任务刚好运行 30 分钟
```

根因：

配置里希望 `GONGKAN_JOBS_DEFAULT_TIMEOUT=0s` 表示不限制任务运行时间，但 Asynq v0.25.1 的 enqueue 行为会把 unset/zero timeout 落回默认 `30m`。因此仅把配置设为 `0s` 不够，必须在 enqueue task 时传一个显式的超长 timeout。

影响：

长任务会被后端任务队列在 30 分钟处取消。前端展示“已取消”是读取后端状态后的结果，不是前端主动制造的状态。

### 6.2 前端进度不显示/一直为 0

症状：

```text
run_state.json 里 fields_completed 已增长到 14
predictions.checkpoint.jsonl 有 14 行
trace.checkpoint.jsonl 有 266 行
DB fill_runs.progress_done 仍为 0
前端进度没有按字段推进
```

根因：

Step15 在运行中写的是 checkpoint 文件，尤其是：

```bash
trace.checkpoint.jsonl
predictions.checkpoint.jsonl
```

原 Go 侧 watcher 只读取最终 `trace.jsonl`，而长任务尚未完成时最终文件可能不存在或不更新，所以 DB 进度没有被刷新。

影响：

即使 Python 侧实际在处理字段，前端也会看起来没有进展。等到 30 分钟 timeout 又把任务杀掉后，前端最终显示 canceled。

### 6.3 旧 run 终止后不可自然恢复

旧 run 的 DB 状态已是 terminal `canceled`，Python 进程也没了。即便 out dir 中有 checkpoint，前端不会自动继续。要继续只能：

1. 部署修复版后新建 run。
2. 或人工设计 requeue/reset 流程，但不能只把 DB 状态改成 running；还必须重新投递 job 或直接启动 CLI，并保证状态一致。

### 6.4 构建过程曾遇到 Docker 镜像拉取 EOF

本地打包期间，曾有一次构建失败在拉取 `python:3.11-slim` 时遇到 Docker mirror EOF。这是构建网络/镜像源问题，不是业务代码失败。后续手动重试 worker build 成功，完整 bundle 也已生成。

## 7. 本地已做修复

本地仓库路径：

```bash
/Users/mao/projects/datacenter
```

### 7.1 任务 timeout 修复

文件：

```bash
go-server/internal/jobs/asynq_queue.go
go-server/internal/jobs/asynq_queue_test.go
```

核心修改：

```go
const asynqPracticalNoTimeout = 100 * 365 * 24 * time.Hour
```

当 `jobsCfg.DefaultTimeout.Duration <= 0` 时，不再传 `0`，而是传 `asynqPracticalNoTimeout`，避免 Asynq 使用 30 分钟默认值。

测试覆盖：

```text
TestAsynqQueueZeroTimeoutUsesPracticalNoTimeout
TestAsynqQueuePositiveTimeoutIsPreserved
```

### 7.2 checkpoint trace 进度修复

文件：

```bash
go-server/internal/jobs/python_handlers.go
go-server/tests/python_job_handler_test.go
go-server/tests/fill_form_worker_integration_test.go
```

核心修改：

```go
func step15TracePaths(outDir string) []string {
    return []string{
        filepath.Join(outDir, "trace.checkpoint.jsonl"),
        filepath.Join(outDir, "trace.jsonl"),
    }
}
```

新增 watcher 会扫描两个 trace 文件，遇到 `step = "field_completed"` 时：

1. 用 `seen[field_id]` 去重。
2. 增加 `done`。
3. 调用 `MarkFillRunProgress(ctx, runID, done, total)` 更新 DB。
4. emit progress event。

测试覆盖：

```text
TestFillFormPythonHandlerReadsCheckpointTraceProgress
```

### 7.3 本地验证

当前会话已重新验证：

```bash
cd /Users/mao/projects/datacenter/go-server
go test ./internal/jobs ./tests
```

结果：

```text
ok github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs
ok github.com/DonJonMao/nested-doc-rag/go-server/tests
```

之前会话还报告通过过：

```bash
go test ./tests ./internal/jobs ./internal/python ./internal/form ./internal/modelgateway
pytest tests/test_config.py tests/test_step15_agent_runner.py tests/test_model_gateway.py
npm run build
```

接管者如需发布前确认，应重新跑一遍，不要只依赖历史报告。

## 8. 本地已生成的修复版 bundle

修复版 tag：

```bash
writeback-37-livebase-4mode-qdrantfix-nojudge-noasynqtimeout-progressfix-20260710
```

本地 bundle：

```bash
/Users/mao/projects/datacenter/artifacts/server_bundle_writeback37_livebase_4mode_qdrantfix_nojudge_arm64.tar.gz
```

文件大小与时间：

```text
server_bundle_writeback37_livebase_4mode_qdrantfix_nojudge_arm64.tar.gz = 3.2G, 2026-07-10 18:31 CST
gongkan-dependencies-linux-arm64-images.tar                         = 328M
gongkan-writeback37-livebase-4mode-qdrantfix-nojudge-linux-arm64-images.tar = 2.9G
```

校验和：

```text
9756c50cf8eef174cd31c8cd98e100de83819f974e91c57be527974cdbe563aa  images/gongkan-dependencies-linux-arm64-images.tar
75b2a25d4d950802c2105012dc34fa4fc636a467965b3454a593038e4b6f0183  images/gongkan-writeback37-livebase-4mode-qdrantfix-nojudge-linux-arm64-images.tar
```

bundle 内 `.env.prod` 已包含：

```bash
IMAGE_TAG=writeback-37-livebase-4mode-qdrantfix-nojudge-noasynqtimeout-progressfix-20260710
GONGKAN_JOBS_DEFAULT_TIMEOUT=0s
GONGKAN_PYTHON_DEFAULT_TIMEOUT=0s
```

注意：`GONGKAN_JOBS_DEFAULT_TIMEOUT=0s` 仍然保留在配置里；修复点在代码层，会把 `0s` 转换成显式超长 Asynq timeout。

## 9. 仍需在 74 机器确认的关键问题

用户反馈“现在还是有前端不显示任务进度的问题”。接管者必须先区分是哪一种：

1. 74 还没有部署修复版镜像，所以仍在旧行为。
2. 已部署修复版，但 worker 容器实际没有使用新 tag。
3. worker 使用新 tag，DB `progress_done` 仍不更新，说明 watcher/handler 仍未生效。
4. DB `progress_done` 已更新，但前端不展示，说明是 API polling/store/view 层问题。

不要跳过这个分层判断。

## 10. 接管后建议先执行的 74 机器命令

### 10.1 确认当前容器和镜像 tag

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

重点看：

```text
gongkan-api-1
gongkan-worker-1
gongkan-postgres-1
gongkan-redis-1
gongkan-qdrant-1
gongkan-minio-1
gongkan-caddy-1
```

确认 worker/API 环境：

```bash
sudo docker inspect gongkan-worker-1 --format '{{.Config.Image}}'
sudo docker inspect gongkan-api-1 --format '{{.Config.Image}}'

sudo docker inspect gongkan-worker-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E 'IMAGE_TAG|GONGKAN_JOBS_DEFAULT_TIMEOUT|GONGKAN_PYTHON_DEFAULT_TIMEOUT|GONGKAN_JOBS'
```

如果 image tag 不是：

```bash
writeback-37-livebase-4mode-qdrantfix-nojudge-noasynqtimeout-progressfix-20260710
```

则 74 上还不是本次修复版。

### 10.2 确认容器内代码是否包含 checkpoint watcher

```bash
sudo docker exec gongkan-worker-1 sh -lc "
  grep -R \"trace.checkpoint.jsonl\" -n /app 2>/dev/null | head -20
  grep -R \"asynqPracticalNoTimeout\" -n /app 2>/dev/null | head -20
"
```

如果 grep 不到，说明当前容器不是修复版或镜像内容不对。

### 10.3 对新 run 观察 DB 进度与事件

新建 run 后替换 `RUN=...`：

```bash
RUN=<new-run-id>
OUT=/app/python-core/artifacts/runs/$RUN

sudo docker exec -i gongkan-postgres-1 psql -U gongkan -d gongkan -x -c "
  SELECT
    id, status, progress_done, progress_total,
    error_message, job_id, out_dir,
    created_at, queued_at, started_at, finished_at, updated_at
  FROM fill_runs
  WHERE id = '$RUN';
"

sudo docker exec -i gongkan-postgres-1 psql -U gongkan -d gongkan -x -c "
  SELECT
    id, status, attempt, max_attempts,
    error_message, cancel_requested_at,
    queued_at, started_at, heartbeat_at, finished_at, updated_at
  FROM jobs
  WHERE resource_id = '$RUN'
     OR id = (SELECT job_id FROM fill_runs WHERE id = '$RUN');
"

sudo docker exec -i gongkan-postgres-1 psql -U gongkan -d gongkan -P pager=off -c "
  SELECT sequence, event_type, created_at, payload_json
  FROM run_events
  WHERE run_id = '$RUN'
  ORDER BY sequence DESC
  LIMIT 40;
"
```

同时看 checkpoint：

```bash
sudo docker exec gongkan-worker-1 sh -lc "
  date
  cat '$OUT/run_state.json' || true
  wc -l '$OUT'/*.checkpoint.jsonl || true
  stat '$OUT/predictions.checkpoint.jsonl' '$OUT/trace.checkpoint.jsonl' || true
  tail -n 5 '$OUT/predictions.checkpoint.jsonl' || true
"
```

判断：

1. 如果 checkpoint 行数增长，但 `fill_runs.progress_done` 不增长，问题在 worker 后端进度 watcher 或 lifecycle 写入。
2. 如果 `fill_runs.progress_done` 增长，但前端不显示，问题在 API/frontend polling/store/view。
3. 如果 30 分钟后仍被 canceled，说明 Asynq timeout 修复没有真正部署到 worker/API 对应的 enqueue 代码路径。

### 10.4 查看 worker 日志

```bash
sudo docker logs --since 2h gongkan-worker-1 | tail -n 300
```

重点搜索：

```bash
sudo docker logs --since 2h gongkan-worker-1 2>&1 \
  | grep -Ei 'timeout|cancel|canceled|progress|trace.checkpoint|field_completed|run-step15-agent|error|panic'
```

## 11. 如果要部署本地修复版到 74

从本地把 bundle 拷到 74 后，在 74 上执行：

```bash
tar -xzf server_bundle_writeback37_livebase_4mode_qdrantfix_nojudge_arm64.tar.gz
cd server_bundle_writeback37_livebase_4mode_qdrantfix_nojudge_arm64
./scripts/00-load-images.sh
./scripts/01-start.sh
./scripts/02-status.sh
```

部署后必须再次确认容器实际镜像 tag：

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
```

## 12. 新 Codex 会话需要注意的开放问题

1. 74 当前是否已经部署 `noasynqtimeout-progressfix-20260710` 修复版？未知，必须查。
2. 用户说“现在还是有前端不显示任务进度”，指的是旧 run 还是部署后新 run？未知，必须问清或从最新 run 查询。
3. 如果修复版已部署但进度仍不显示，需要判断 DB 是否更新；不要直接改前端。
4. 旧 run `4e01a7aa-c68c-41d5-8dd1-6c7c1065bb75` 只能作为事故样本，不适合用来验证修复是否生效。
5. 当前本地 git worktree 有大量既有改动，不要随意 revert。只处理与 timeout/progress/bundle 相关文件。

## 13. 建议给用户的简短说明口径

可以这样解释：

```text
这次“已取消”不是前端点取消，而是后端任务队列在 30 分钟处把长任务超时取消。前端只是展示 DB 里的 canceled 状态。

进度不显示是另一个问题：Step15 运行中写 checkpoint trace，Go 侧原来只看最终 trace，所以 DB progress_done 没有更新。修复版已让 worker 同时读取 trace.checkpoint.jsonl 和 trace.jsonl。

下一步需要先确认 74 当前容器是否已经使用修复版 tag。如果未部署修复版，先部署；如果已部署仍不显示进度，就查 DB progress_done 和 run_events，判断是后端没写进度还是前端没渲染。
```

