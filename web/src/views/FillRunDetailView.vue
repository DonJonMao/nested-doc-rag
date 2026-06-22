<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import SubNav from '@/components/nav/SubNav.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import ArtifactDownloadPanel from '@/components/fill/ArtifactDownloadPanel.vue'
import RunEventTimeline from '@/components/fill/RunEventTimeline.vue'
import { subscribeRunEvents } from '@/api/events.api'
import { downloadEvidenceImage } from '@/api/fillRuns.api'
import { useFillRunStore } from '@/stores/fillRun.store'
import type { FillRunEvidenceRef, RunEvent } from '@/api/types'

const route = useRoute()
const fill = useFillRunStore()
const events = ref<RunEvent[]>([])
const controller = ref<AbortController | null>(null)
const loadError = ref('')
const terminalStatuses = ['completed', 'succeeded', 'completed_with_failures', 'failed', 'cancelled', 'canceled']

const runId = computed(() => String(route.params.runId))
const run = computed(() => fill.detail)
const percent = computed(() => {
  if (!run.value) return 0
  return terminalStatuses.includes(run.value.status) ? 100 : 35
})
const isProcessing = computed(() => ['created', 'queued', 'running', 'cancel_requested'].includes(run.value?.status || ''))
const isCompletedWithFailures = computed(() => run.value?.status === 'completed_with_failures')
const isFailed = computed(() => run.value?.status === 'failed')
const artifactInvalid = computed(() => run.value?.artifact_validation_status === 'invalid' || run.value?.manifest_status === 'invalid')
const canCancel = computed(() => ['queued', 'running'].includes(run.value?.raw_status || run.value?.status || ''))
const uncertainFields = computed(() => run.value?.writeback?.fields?.filter((field) => field.status === 'uncertain') ?? [])

function connectEvents() {
  if (!run.value?.workspace_id) return
  controller.value?.abort()
  controller.value = new AbortController()
  subscribeRunEvents({
    runId: run.value.id,
    workspaceId: run.value.workspace_id,
    afterSequence: events.value.at(-1)?.sequence,
    signal: controller.value.signal,
    onEvent(event) {
      if (!events.value.some((item) => item.sequence === event.sequence)) {
        events.value.push(event)
      }
      if (shouldRefreshRun(event.event_type)) {
        fill.loadRun(runId.value).catch(() => undefined)
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
  try {
    loadError.value = ''
    await fill.loadRun(runId.value)
    connectEvents()
  } catch {
    loadError.value = '任务不存在或无权限访问'
  }
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

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '-'
  return String(value)
}

function evidenceSource(ref: FillRunEvidenceRef) {
  return ref.file_name || ref.document_id || ref.object_key || ref.chunk_id || '未知来源'
}

function evidenceLocation(ref: FillRunEvidenceRef) {
  return [ref.source_anchor, ref.sheet_name, ref.cell, ref.page ? `page ${ref.page}` : ''].filter(Boolean).join(' / ') || '-'
}

async function downloadImage(imageObjectKey: string) {
  if (!run.value) return
  try {
    await downloadEvidenceImage(run.value.id, imageObjectKey)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '图片证据下载失败')
  }
}

onMounted(load)
onBeforeUnmount(() => controller.value?.abort())
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
      <el-progress :percentage="percent" />
      <el-button v-if="canCancel" @click="cancel">取消任务</el-button>
      <p v-if="isProcessing" class="detail__info">任务处理中</p>
      <p v-if="isCompletedWithFailures" class="detail__warning">任务已完成，但部分字段处理失败，请查看需人工补充字段清单。</p>
      <p v-if="isFailed" class="detail__error">{{ run.error_message || '任务失败，无法下载结果。' }}</p>
      <p v-if="artifactInvalid" class="detail__error">结果文件校验失败，请重新运行任务或联系管理员。</p>
    </section>

    <section v-if="run" class="detail__notice gk-card">
      {{ run.message || '该表格仅自动写入系统判定为安全的字段；未写入或需复核字段请人工补充。' }}
    </section>

    <section v-if="run" class="detail__cards">
      <div class="detail__metric gk-card">
        <span>总字段数</span>
        <strong>{{ run.summary.total_fields }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>已回答字段</span>
        <strong>{{ run.summary.answered }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>自动写入字段</span>
        <strong>{{ count(run.summary.written ?? run.summary.writeback_allowed) }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>需人工补充/复核字段</span>
        <strong>{{ run.summary.review_required }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>确认字段</span>
        <strong>{{ count(run.summary.confirmed) }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>存疑字段</span>
        <strong>{{ count(run.summary.uncertain) }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>标记字段</span>
        <strong>{{ count(run.summary.flagged) }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>未找到字段</span>
        <strong>{{ run.summary.not_found }}</strong>
      </div>
      <div class="detail__metric gk-card">
        <span>失败字段</span>
        <strong>{{ run.summary.failed_fields }}</strong>
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

    <section v-if="run && uncertainFields.length" class="detail__evidence gk-card">
      <h2 class="gk-card-title">存疑字段证据</h2>
      <p class="gk-caption">以下字段已按配置标红写入或进入人工补充清单，请下载表格后线下复核。</p>
      <div class="detail__evidence-list">
        <article v-for="field in uncertainFields" :key="field.field_key || field.field_id || field.target_cell" class="detail__evidence-item">
          <div class="detail__evidence-head">
            <strong>{{ field.field_key || field.field_id || field.target_cell }}</strong>
            <span>{{ field.writeback_action || 'review_only' }}</span>
          </div>
          <div class="detail__evidence-answer">{{ displayValue(field.answer_value) }}</div>
          <div v-for="(ref, index) in field.evidence_refs" :key="`${field.field_key || field.field_id}-${index}`" class="detail__evidence-ref">
            <div>{{ evidenceSource(ref) }}</div>
            <small>{{ evidenceLocation(ref) }}</small>
            <p v-if="ref.text_preview">{{ ref.text_preview }}</p>
            <el-button
              v-if="ref.image_object_key"
              link
              type="primary"
              @click="downloadImage(ref.image_object_key || '')"
            >
              下载图片证据
            </el-button>
          </div>
        </article>
      </div>
    </section>

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
}

.detail__summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 18px;
}

.detail__error {
  grid-column: 1 / -1;
  color: var(--gk-danger);
}

.detail__warning {
  grid-column: 1 / -1;
  color: var(--gk-warning);
}

.detail__info {
  grid-column: 1 / -1;
  color: var(--gk-info);
}

.detail__notice {
  color: var(--gk-ink-2);
  line-height: 1.6;
}

.detail__cards {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
}

.detail__evidence {
  padding: 24px;
}

.detail__evidence-list {
  display: grid;
  gap: 12px;
  margin-top: 16px;
}

.detail__evidence-item {
  border: 1px solid var(--gk-border);
  border-radius: 8px;
  padding: 16px;
  display: grid;
  gap: 10px;
}

.detail__evidence-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.detail__evidence-head span {
  color: var(--gk-warning);
}

.detail__evidence-answer {
  color: var(--gk-ink-1);
}

.detail__evidence-ref {
  border-top: 1px solid var(--gk-border);
  padding-top: 10px;
  display: grid;
  gap: 4px;
}

.detail__evidence-ref small {
  color: var(--gk-ink-3);
}

.detail__evidence-ref p {
  margin: 0;
  color: var(--gk-ink-2);
}

.detail__metric {
  min-height: 104px;
  display: grid;
  align-content: space-between;
}

.detail__metric span {
  color: var(--gk-ink-3);
  font-size: 13px;
}

.detail__metric strong {
  font-size: 30px;
  line-height: 1;
}

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

@media (max-width: 980px) {
  .detail__cards {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .detail__summary {
    grid-template-columns: 1fr;
  }

  .detail__cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
