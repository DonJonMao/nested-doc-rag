<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import SubNav from '@/components/nav/SubNav.vue'
import KnowledgeBaseSelector from '@/components/fill/KnowledgeBaseSelector.vue'
import AppleUploadDropzone from '@/components/upload/AppleUploadDropzone.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { useWorkspaceStore } from '@/stores/workspace.store'
import { useKnowledgeStore } from '@/stores/knowledge.store'
import { useFillRunStore } from '@/stores/fillRun.store'

const router = useRouter()
const workspace = useWorkspaceStore()
const knowledge = useKnowledgeStore()
const fill = useFillRunStore()
const taskName = ref('')
const roomContext = ref('')
const busy = ref(false)

const workspaceId = computed(() => workspace.currentWorkspace?.id || workspace.currentWorkspaceId)
const selectedReady = computed(() => knowledge.options.find((item) => item.id === knowledge.selectedId && item.status === 'ready'))

async function ensureData() {
  if (!workspace.workspaces.length) await workspace.load()
  if (workspaceId.value) await knowledge.loadOptions(workspaceId.value)
}

async function onFile(file: File) {
  if (!workspaceId.value) {
    ElMessage.error('缺少工作区')
    return
  }
  busy.value = true
  try {
    await fill.upload(workspaceId.value, file)
    if (!taskName.value.trim()) {
      taskName.value = file.name.replace(/\.[^.]+$/, '')
    }
    ElMessage.success('工勘单已上传')
  } catch {
    ElMessage.error('上传失败')
  } finally {
    busy.value = false
  }
}

async function start() {
  if (!workspaceId.value || !selectedReady.value || !fill.uploadedForm) return
  busy.value = true
  try {
    const run = await fill.createSimple(workspaceId.value, selectedReady.value.id, fill.uploadedForm.id, taskName.value, roomContext.value)
    router.push({ name: 'fill-run-detail', params: { runId: run.id } })
  } catch {
    ElMessage.error('创建填表任务失败')
  } finally {
    busy.value = false
  }
}

onMounted(ensureData)
</script>

<template>
  <SubNav title="自动填表" subtitle="选择 ready 分库，上传工勘单，启动后由 Worker 在后台执行" />
  <main class="gk-shell gk-main fill-create">
    <section class="fill-create__hero">
      <h1 class="gk-page-title">上传工勘单，自动完成字段填写</h1>
      <p>系统会检索对应资料，调用后端 Step15AgentRunner，并生成可下载的回填 Excel。</p>
    </section>

    <section class="gk-grid-two">
      <div class="gk-card fill-create__panel">
        <h2 class="gk-card-title">选择知识分库</h2>
        <KnowledgeBaseSelector v-model="knowledge.selectedId" :items="knowledge.options" />
      </div>
      <div class="gk-card fill-create__panel">
        <h2 class="gk-card-title">上传工勘单</h2>
        <AppleUploadDropzone accept=".xlsx" title="上传 .xlsx 工勘单" subtitle="浏览器关闭后，后端任务仍会继续" :busy="busy" @selected="onFile" />
        <div v-if="fill.uploadedForm" class="fill-create__uploaded">
          <span>{{ fill.uploadedForm.filename }}</span>
          <StatusPill status="uploaded" />
        </div>
      </div>
    </section>

    <section class="gk-card fill-create__start">
      <div class="fill-create__start-fields">
        <el-input v-model="taskName" size="large" maxlength="120" show-word-limit placeholder="任务名称，例如：西咸4号楼301机房调研" />
        <el-input v-model="roomContext" size="large" placeholder="机房/房间上下文，例如：西咸4号楼 301机房" />
      </div>
      <el-button type="primary" size="large" :loading="busy" :disabled="!selectedReady || !fill.uploadedForm" @click="start">开始填写</el-button>
    </section>
  </main>
</template>

<style scoped>
.fill-create__hero {
  margin-bottom: 32px;
  padding: 8px 2px;
}

.fill-create__hero p {
  max-width: 720px;
  color: var(--gk-ink-3);
  font-size: 18px;
  line-height: 1.5;
}

.fill-create__panel {
  padding: 24px;
  box-shadow: var(--gk-glass-shadow);
}

.fill-create__panel h2 {
  margin-bottom: 18px;
}

.fill-create__uploaded {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--gk-glass-line);
  border-radius: var(--gk-radius-md);
  background: rgba(255, 255, 255, 0.46);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.fill-create__start {
  margin-top: 24px;
  padding: 22px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: start;
  box-shadow: var(--gk-glass-shadow);
}

.fill-create__start-fields {
  display: grid;
  gap: 12px;
}

@media (max-width: 760px) {
  .fill-create__start {
    grid-template-columns: 1fr;
  }
}

@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .fill-create__uploaded {
    -webkit-backdrop-filter: saturate(170%) blur(16px);
    backdrop-filter: saturate(170%) blur(16px);
  }
}
</style>
