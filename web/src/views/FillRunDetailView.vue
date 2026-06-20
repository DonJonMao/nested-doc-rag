<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import SubNav from '@/components/nav/SubNav.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import ArtifactDownloadPanel from '@/components/fill/ArtifactDownloadPanel.vue'
import RunEventTimeline from '@/components/fill/RunEventTimeline.vue'
import { subscribeRunEvents } from '@/api/events.api'
import { useFillRunStore } from '@/stores/fillRun.store'
import { useWorkspaceStore } from '@/stores/workspace.store'
import type { RunEvent } from '@/api/types'

const route = useRoute()
const fill = useFillRunStore()
const workspace = useWorkspaceStore()
const events = ref<RunEvent[]>([])
const controller = ref<AbortController | null>(null)
const terminalStatuses = ['succeeded', 'completed_with_failures', 'failed', 'canceled']

const runId = computed(() => String(route.params.runId))
const percent = computed(() => {
  const run = fill.current
  if (!run) return 0
  if (run.progress_total > 0) return Math.round((run.progress_done / run.progress_total) * 100)
  return ['succeeded', 'completed_with_failures'].includes(run.status) ? 100 : 0
})

function connectEvents() {
  if (!fill.current || !workspace.currentWorkspaceId) return
  controller.value?.abort()
  controller.value = new AbortController()
  subscribeRunEvents({
    runId: fill.current.id,
    workspaceId: workspace.currentWorkspaceId,
    afterSequence: events.value.at(-1)?.sequence,
    signal: controller.value.signal,
    onEvent(event) {
      if (!events.value.some((item) => item.sequence === event.sequence)) {
        events.value.push(event)
      }
      const currentRunId = fill.current!.id
      if (shouldRefreshRun(event.event_type)) {
        fill.loadRun(currentRunId)
          .then((run) => {
            if (terminalStatuses.includes(run.status)) {
              fill.loadResult(currentRunId).catch(() => undefined)
            }
          })
          .catch(() => undefined)
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
  if (!workspace.workspaces.length) await workspace.load()
  await fill.loadRun(runId.value)
  if (terminalStatuses.includes(fill.current?.status || '')) {
    await fill.loadResult(runId.value).catch(() => undefined)
  }
  connectEvents()
}

async function cancel() {
  if (!fill.current) return
  await fill.cancel(fill.current.id)
  ElMessage.success('已请求取消')
}

onMounted(load)
onBeforeUnmount(() => controller.value?.abort())
</script>

<template>
  <SubNav title="任务详情" subtitle="实时进度、事件和结果下载" />
  <main class="gk-shell gk-main detail">
    <section v-if="fill.current" class="detail__summary gk-card">
      <div>
        <h1 class="gk-card-title">{{ fill.current.name || fill.current.target_namespace }}</h1>
        <div class="gk-caption">Run ID: {{ fill.current.id }}</div>
      </div>
      <StatusPill :status="fill.current.status" />
      <el-progress :percentage="percent" />
      <el-button v-if="['queued', 'running'].includes(fill.current.status)" @click="cancel">取消任务</el-button>
      <p v-if="fill.current.error_message" class="detail__error">{{ fill.current.error_message }}</p>
    </section>

    <div v-if="fill.current" class="gk-grid-two">
      <ArtifactDownloadPanel :run="fill.current" />
      <section class="gk-card detail__metrics">
        <h2 class="gk-card-title">运行摘要</h2>
        <dl>
          <dt>进度</dt>
          <dd>{{ fill.current.progress_done }} / {{ fill.current.progress_total }}</dd>
          <dt>行范围</dt>
          <dd>{{ fill.current.rows }}</dd>
          <dt>检索模式</dt>
          <dd>{{ fill.current.retrieval_mode }}</dd>
          <dt>Prompt</dt>
          <dd>{{ fill.current.prompt_version }}</dd>
        </dl>
      </section>
    </div>

    <RunEventTimeline :events="events" />
  </main>
</template>

<style scoped>
.detail {
  display: grid;
  gap: 24px;
}

.detail__summary,
.detail__metrics {
  padding: 24px;
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
</style>
