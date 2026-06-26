<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import SubNav from '@/components/nav/SubNav.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import AppleUploadDropzone from '@/components/upload/AppleUploadDropzone.vue'
import RunEventTimeline from '@/components/fill/RunEventTimeline.vue'
import { downloadWithAuth } from '@/api/http'
import { subscribeRunEvents } from '@/api/events.api'
import { useWorkspaceStore } from '@/stores/workspace.store'
import { useKnowledgeStore } from '@/stores/knowledge.store'
import type { RunEvent } from '@/api/types'

const workspace = useWorkspaceStore()
const knowledge = useKnowledgeStore()
const busy = ref(false)
const ingestionEvents = ref<RunEvent[]>([])
const ingestionController = ref<AbortController | null>(null)
const workspaceId = computed(() => workspace.currentWorkspace?.id || workspace.currentWorkspaceId)
const selected = computed(() => knowledge.options.find((item) => item.id === knowledge.selectedId))
const terminalIngestionEvents = ['index_version_ready', 'ingestion_failed', 'failed', 'canceled', 'succeeded']

async function load() {
  if (!workspace.workspaces.length) await workspace.load()
  if (workspaceId.value) await knowledge.loadOptions(workspaceId.value)
  if (knowledge.selectedId) await knowledge.loadDocuments()
}

async function upload(file: File) {
  if (!selected.value) return
  busy.value = true
  try {
    await knowledge.uploadDocument(selected.value.id, file)
    await knowledge.loadOptions(workspaceId.value)
    ElMessage.success('文档已上传，入库任务已创建')
  } catch {
    ElMessage.error('上传或自动入库失败')
  } finally {
    busy.value = false
  }
}

async function remove(docId: string) {
  busy.value = true
  try {
    await knowledge.deleteDocument(docId)
    await knowledge.loadOptions(workspaceId.value)
    ElMessage.success('文档已删除，索引刷新任务已创建')
  } catch {
    ElMessage.error('删除或刷新索引失败')
  } finally {
    busy.value = false
  }
}

function connectIngestionEvents() {
  const ingestion = knowledge.latestIngestion
  if (!ingestion || !workspaceId.value) return
  ingestionController.value?.abort()
  ingestionController.value = new AbortController()
  subscribeRunEvents({
    runId: ingestion.id,
    workspaceId: workspaceId.value,
    afterSequence: ingestionEvents.value.at(-1)?.sequence,
    signal: ingestionController.value.signal,
    onEvent(event) {
      if (!ingestionEvents.value.some((item) => item.sequence === event.sequence)) {
        ingestionEvents.value.push(event)
      }
      if (terminalIngestionEvents.some((type) => event.event_type === type || event.event_type.endsWith(`.${type}`))) {
        if (workspaceId.value) knowledge.loadOptions(workspaceId.value).catch(() => undefined)
        knowledge.loadDocuments().catch(() => undefined)
      }
    },
    onError() {
      ElMessage.warning('入库事件连接中断，正在等待重连')
    },
  }).catch(() => undefined)
}

watch(() => knowledge.selectedId, (id) => {
  if (id) knowledge.loadDocuments(id)
})

watch(() => knowledge.latestIngestion?.id, () => {
  ingestionEvents.value = []
  connectIngestionEvents()
})

onMounted(load)
onBeforeUnmount(() => ingestionController.value?.abort())
</script>

<template>
  <SubNav title="知识库管理" subtitle="上传知识文档，自动入库，维护分库 ready 状态" />
  <main class="gk-shell gk-main admin-knowledge">
    <aside class="admin-knowledge__rail gk-card">
      <button v-for="item in knowledge.options" :key="item.id" class="admin-knowledge__kb" :class="{ active: item.id === knowledge.selectedId }" type="button" @click="knowledge.selectedId = item.id">
        <span>{{ item.name }}</span>
        <StatusPill :status="item.status" />
        <small>{{ item.document_count }} 份文档</small>
      </button>
    </aside>

    <section class="admin-knowledge__detail">
      <div v-if="selected" class="gk-card admin-knowledge__header">
        <div>
          <h1 class="gk-card-title">{{ selected.name }}</h1>
          <p class="gk-caption">{{ selected.namespace }} · {{ selected.qdrant_collection }}</p>
        </div>
        <StatusPill :status="selected.status" />
      </div>

      <div class="gk-card admin-knowledge__upload">
        <h2 class="gk-card-title">上传知识文档</h2>
        <AppleUploadDropzone accept=".xlsx,.xlsm,.docx,.txt,.md,.csv" title="上传知识文档" subtitle="上传后会自动创建 ingestion run" :busy="busy" @selected="upload" />
      </div>

      <div v-if="knowledge.latestIngestion" class="gk-card admin-knowledge__ingestion">
        <div>
          <h2 class="gk-card-title">最近入库任务</h2>
          <p class="gk-caption">{{ knowledge.latestIngestion.id }}</p>
        </div>
        <StatusPill :status="knowledge.latestIngestion.status" />
      </div>

      <RunEventTimeline v-if="knowledge.latestIngestion" :events="ingestionEvents" />

      <div class="gk-card admin-knowledge__table">
        <h2 class="gk-card-title">文档列表</h2>
        <el-table :data="knowledge.documents" style="width: 100%">
          <el-table-column prop="filename" label="文件名" min-width="220" />
          <el-table-column prop="namespace" label="namespace" width="170" />
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <StatusPill :status="row.status" />
            </template>
          </el-table-column>
          <el-table-column label="上传时间" width="180">
            <template #default="{ row }">{{ dayjs(row.created_at).format('YYYY-MM-DD HH:mm') }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <el-button link type="primary" @click="downloadWithAuth(`/api/v1/files/${row.file_id}/download`, row.filename)">下载</el-button>
              <el-button link type="danger" :disabled="busy" @click="remove(row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>
  </main>
</template>

<style scoped>
.admin-knowledge {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 24px;
}

.admin-knowledge__rail,
.admin-knowledge__header,
.admin-knowledge__upload,
.admin-knowledge__ingestion,
.admin-knowledge__table {
  padding: 20px;
  box-shadow: var(--gk-glass-shadow-soft);
}

.admin-knowledge__rail {
  align-self: start;
  display: grid;
  gap: 10px;
}

.admin-knowledge__kb {
  min-height: 82px;
  padding: 14px;
  border: 1px solid var(--gk-glass-line);
  border-radius: var(--gk-radius-md);
  background: rgba(255, 255, 255, 0.56);
  text-align: left;
  display: grid;
  gap: 6px;
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.66);
  transition: border-color 160ms var(--gk-ease), background-color 160ms var(--gk-ease), box-shadow 160ms var(--gk-ease), transform 160ms var(--gk-ease);
}

.admin-knowledge__kb:hover {
  background: rgba(255, 255, 255, 0.76);
  border-color: rgba(0, 102, 204, 0.2);
}

.admin-knowledge__kb:active {
  transform: scale(0.99);
}

.admin-knowledge__kb.active {
  border-color: rgba(0, 102, 204, 0.4);
  background: linear-gradient(135deg, rgba(234, 244, 255, 0.82), rgba(255, 255, 255, 0.58));
  box-shadow: 0 0 0 2px rgba(0, 102, 204, 0.16) inset, 0 8px 20px rgba(0, 102, 204, 0.08);
}

.admin-knowledge__kb small {
  color: var(--gk-ink-3);
}

.admin-knowledge__detail {
  display: grid;
  gap: 20px;
}

.admin-knowledge__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.admin-knowledge__ingestion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.admin-knowledge__upload h2,
.admin-knowledge__table h2 {
  margin-bottom: 16px;
}

@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .admin-knowledge__kb {
    -webkit-backdrop-filter: saturate(170%) blur(16px);
    backdrop-filter: saturate(170%) blur(16px);
  }
}

@media (max-width: 900px) {
  .admin-knowledge {
    grid-template-columns: 1fr;
  }
}
</style>
