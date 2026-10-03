<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import SubNav from '@/components/nav/SubNav.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import ArtifactDownloadPanel from '@/components/fill/ArtifactDownloadPanel.vue'
import EvidenceWorkbench from '@/components/fill/EvidenceWorkbench.vue'
import RunEventTimeline from '@/components/fill/RunEventTimeline.vue'
import { subscribeRunEvents } from '@/api/events.api'
import { downloadEvidenceImage } from '@/api/fillRuns.api'
import { useFillRunStore } from '@/stores/fillRun.store'
import type { RunEvent } from '@/api/types'

const route = useRoute()
const fill = useFillRunStore()
const events = ref<RunEvent[]>([])
const controller = ref<AbortController | null>(null)
const loadError = ref('')
const completedStatuses = ['completed', 'succeeded', 'completed_with_failures']
let pollTimer: ReturnType<typeof setInterval> | null = null
let refreshPromise: Promise<unknown> | null = null

const runId = computed(() => String(route.params.runId))
const run = computed(() => fill.detail?.id === runId.value ? fill.detail : null)
const percent = computed(() => {
  if (!run.value) return 0
  const total = run.value.progress_total || 0
  const done = run.value.progress_done || 0
  if (total > 0) {
    const cap = completedStatuses.includes(run.value.status) ? 100 : 99
    return Math.max(done > 0 ? 1 : 0, Math.min(cap, Math.round((done / total) * 100)))
  }
  if (completedStatuses.includes(run.value.status)) return 100
  return 0
})
const isProcessing = computed(() => ['created', 'queued', 'running', 'cancel_requested'].includes(run.value?.status || ''))
const isCompletedWithFailures = computed(() => run.value?.status === 'completed_with_failures')
const isFailed = computed(() => run.value?.status === 'failed')
const isCanceled = computed(() => ['cancelled', 'canceled'].includes(run.value?.status || ''))
const artifactInvalid = computed(() => run.value?.artifact_validation_status === 'invalid' || run.value?.manifest_status === 'invalid')
const canCancel = computed(() => ['queued', 'running'].includes(run.value?.raw_status || run.value?.status || ''))

function connectEvents() {
  if (!run.value?.workspace_id) return
  const subscribedRunId = run.value.id
  controller.value?.abort()
  controller.value = new AbortController()
  subscribeRunEvents({
    runId: run.value.id,
    workspaceId: run.value.workspace_id,
    afterSequence: events.value.at(-1)?.sequence,
    signal: controller.value.signal,
    onEvent(event) {
      if (runId.value !== subscribedRunId) return
      if (!events.value.some((item) => item.sequence === event.sequence)) {
        events.value.push(event)
      }
      if (shouldRefreshRun(event.event_type)) {
        refreshRun()
      }
    },
    onError() {
      ElMessage.warning('运行事件连接中断，正在等待重连')
    },
  }).catch(() => undefined)
}

function shouldRefreshRun(eventType: string) {
  return [
    'queued',
    'running',
    'progress',
    'succeeded',
    'completed_with_failures',
    'failed',
    'cancel_requested',
    'canceled',
    'artifacts_registered',
    'review_items_imported',
  ].some((type) => eventType === type || eventType.endsWith(`.${type}`))
}

async function load() {
  const requestedRunId = runId.value
  controller.value?.abort()
  events.value = []
  if (pollTimer) clearInterval(pollTimer)
  try {
    loadError.value = ''
    await fill.loadRun(requestedRunId)
    if (requestedRunId !== runId.value) return
    connectEvents()
    startPolling()
  } catch {
    if (requestedRunId !== runId.value) return
    loadError.value = '任务不存在或无权限访问'
  }
}

function refreshRun() {
  if (refreshPromise) return refreshPromise
  refreshPromise = fill.loadRun(runId.value).catch(() => undefined).finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(() => {
    if (isProcessing.value) refreshRun()
  }, 5000)
}

async function cancel() {
  if (!run.value) return
  await fill.cancel(run.value.id)
  await fill.loadRun(run.value.id).catch(() => undefined)
  ElMessage.success('已请求取消')
}

function count(value?: number) {
  return value ?? 0
}

async function downloadImage(imageObjectKey: string) {
  if (!run.value) return
  try {
    await downloadEvidenceImage(run.value.id, imageObjectKey)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '图片证据下载失败')
  }
}

watch(runId, load, { immediate: true })
onBeforeUnmount(() => {
  controller.value?.abort()
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <SubNav title="任务详情" subtitle="查看任务状态，下载安全自动填写版结果" />
  <main class="gk-shell gk-main detail">
    <section v-if="loadError" class="detail__error-card gk-card">
      {{ loadError }}
    </section>

    <section v-if="run" class="detail__summary gk-card">
      <div>
        <h1 class="gk-card-title">{{ run.name || run.template_file_name || run.id }}</h1>
        <div class="gk-caption">Run ID: {{ run.id }}</div>
        <div class="gk-caption">{{ run.template_file_name || '未记录模板文件' }} · {{ run.kb_name || '未关联知识库' }}</div>
      </div>
      <StatusPill :status="run.status" />
      <div class="detail__progress">
        <el-progress v-if="run.progress_total > 0 || completedStatuses.includes(run.status)" :percentage="percent" />
        <p v-else class="gk-caption">{{ isProcessing ? '等待字段总数与执行进度' : '未记录字段进度' }}</p>
        <span v-if="run.progress_total > 0">已处理 {{ run.progress_done }} / {{ run.progress_total }}</span>
      </div>
      <el-button v-if="canCancel" @click="cancel">取消任务</el-button>
      <p v-if="isProcessing" class="detail__info">任务处理中</p>
      <p v-if="isCompletedWithFailures" class="detail__warning">任务已完成，但部分字段处理失败，请查看需人工补充字段清单。</p>
      <p v-if="isFailed" class="detail__error">{{ run.error_message || '任务失败，无法下载结果。' }}</p>
      <p v-if="isCanceled" class="detail__warning">任务已取消，未生成可下载结果。</p>
      <p v-if="artifactInvalid" class="detail__error">结果文件校验失败，请重新运行任务或联系管理员。</p>
    </section>

    <section v-if="run" class="detail__notice gk-card">
      {{ run.message || '该表格仅自动写入系统判定为安全的字段；未写入或需复核字段请人工补充。' }}
    </section>

    <section v-if="run" class="detail__outcomes" aria-label="任务字段概览">
      <div class="detail__cards">
        <div class="detail__metric gk-card">
          <span>总字段数</span>
          <strong>{{ run.summary.total_fields }}</strong>
          <small>本次任务字段</small>
        </div>
        <div class="detail__metric gk-card">
          <span>已回答</span>
          <strong>{{ run.summary.answered }}</strong>
          <small>回答与写回判定独立</small>
        </div>
        <div class="detail__metric detail__metric--written gk-card">
          <span>实际写入</span>
          <strong>{{ run.summary.written ?? '—' }}</strong>
          <small>{{ run.summary.written === undefined ? '未记录实际写入数' : '以写回审计为准' }}</small>
        </div>
        <div class="detail__metric detail__metric--review gk-card">
          <span>需人工补充 / 复核</span>
          <strong>{{ run.summary.review_required }}</strong>
          <small>下载后线下核对</small>
        </div>
      </div>
      <div class="detail__breakdown gk-card">
        <span class="detail__breakdown-label">字段判定</span>
        <span class="detail__outcome detail__outcome--confirmed"><i aria-hidden="true"></i>确认 <b>{{ count(run.summary.confirmed) }}</b></span>
        <span class="detail__outcome detail__outcome--uncertain"><i aria-hidden="true"></i>存疑 <b>{{ count(run.summary.uncertain) }}</b></span>
        <span class="detail__outcome detail__outcome--flagged"><i aria-hidden="true"></i>标记 <b>{{ count(run.summary.flagged) }}</b></span>
        <span class="detail__outcome"><i aria-hidden="true"></i>未找到 <b>{{ run.summary.not_found }}</b></span>
        <span class="detail__outcome detail__outcome--flagged"><i aria-hidden="true"></i>失败 <b>{{ run.summary.failed_fields }}</b></span>
      </div>
    </section>

    <div v-if="run" class="gk-grid-two">
      <ArtifactDownloadPanel :run="run" />
      <section class="gk-card detail__metrics">
        <h2 class="gk-card-title">结果状态</h2>
        <dl>
          <dt>Manifest</dt>
          <dd>{{ run.manifest_status }}</dd>
          <dt>Artifact</dt>
          <dd>{{ run.artifact_validation_status }}</dd>
          <dt>创建时间</dt>
          <dd>{{ new Date(run.created_at).toLocaleString() }}</dd>
          <dt>完成时间</dt>
          <dd>{{ run.completed_at ? new Date(run.completed_at).toLocaleString() : '-' }}</dd>
        </dl>
      </section>
    </div>

    <EvidenceWorkbench
      v-if="run"
      :evidence="run.evidence"
      :writeback="run.writeback"
      :run-status="run.status"
      :artifact-invalid="artifactInvalid"
      @download-image="downloadImage"
    />

    <RunEventTimeline v-if="run" :events="events" />
  </main>
</template>

<style scoped>
.detail {
  display: grid;
  gap: 24px;
}

.detail__summary,
.detail__metrics,
.detail__notice,
.detail__metric,
.detail__error-card {
  padding: 24px;
}

.detail__error-card {
  color: var(--gk-danger);
  background: rgba(255, 240, 239, 0.84);
}

.detail__summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 18px;
  box-shadow: var(--gk-glass-shadow);
}

.detail__summary > div:first-child { min-width: 0; overflow-wrap: anywhere; }

.detail__progress {
  min-width: 0;
}

.detail__progress span {
  display: block;
  margin-top: 6px;
  color: var(--gk-ink-3);
  font-size: 13px;
}

.detail__error {
  grid-column: 1 / -1;
  color: var(--gk-danger);
  margin: 0;
  padding: 12px 14px;
  border: 1px solid rgba(201, 52, 43, 0.16);
  border-radius: var(--gk-radius-md);
  background: rgba(255, 240, 239, 0.66);
}

.detail__warning {
  grid-column: 1 / -1;
  color: var(--gk-warning);
  margin: 0;
  padding: 12px 14px;
  border: 1px solid rgba(179, 107, 0, 0.16);
  border-radius: var(--gk-radius-md);
  background: rgba(255, 247, 232, 0.68);
}

.detail__info {
  grid-column: 1 / -1;
  color: var(--gk-info);
  margin: 0;
  padding: 12px 14px;
  border: 1px solid rgba(0, 102, 204, 0.14);
  border-radius: var(--gk-radius-md);
  background: rgba(234, 244, 255, 0.7);
}

.detail__notice {
  color: var(--gk-ink-2);
  line-height: 1.6;
  background: linear-gradient(135deg, rgba(234, 244, 255, 0.74), rgba(255, 255, 255, 0.58));
}

.detail__outcomes {
  display: grid;
  gap: 12px;
}

.detail__cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.detail__metric {
  min-width: 0;
  min-height: 142px;
  display: grid;
  align-content: space-between;
  gap: 12px;
  background: var(--gk-glass-bg-strong);
}

.detail__metric span {
  color: var(--gk-ink-2);
  font-size: 13px;
}

.detail__metric strong {
  font-size: 34px;
  line-height: 1;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.detail__metric small {
  color: var(--gk-ink-3);
  font-size: 11px;
}

.detail__metric--written strong { color: var(--gk-info); }
.detail__metric--review strong { color: var(--gk-warning); }

.detail__breakdown {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px 24px;
  padding: 15px 22px;
  box-shadow: none;
}

.detail__breakdown-label {
  font-size: 12px;
  color: var(--gk-ink-3);
  margin-right: 8px;
}

.detail__outcome {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--gk-ink-3);
  font-size: 12px;
}

.detail__outcome i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.detail__outcome b {
  color: var(--gk-ink-1);
  font-size: 14px;
  margin-left: 3px;
  font-variant-numeric: tabular-nums;
}

.detail__outcome--confirmed { color: var(--gk-success); }
.detail__outcome--uncertain { color: var(--gk-warning); }
.detail__outcome--flagged { color: var(--gk-danger); }

.detail__metrics dl {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 12px;
}

.detail__metrics dt {
  color: var(--gk-ink-3);
}

.detail__metrics dd {
  margin: 0;
}

@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .detail__error,
  .detail__warning,
  .detail__info {
    -webkit-backdrop-filter: saturate(170%) blur(16px);
    backdrop-filter: saturate(170%) blur(16px);
  }
}

@media (max-width: 980px) {
  .detail__cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .detail__summary {
    grid-template-columns: 1fr;
  }

  .detail__cards { gap: 9px; }

  .detail__metric { padding: 19px 16px; min-height: 130px; }
  .detail__metric strong { font-size: 29px; }
  .detail__breakdown { padding: 14px 16px; gap: 12px 17px; }
  .detail__breakdown-label { width: 100%; }
}
</style>
