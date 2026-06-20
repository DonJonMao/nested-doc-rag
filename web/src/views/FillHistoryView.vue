<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import SubNav from '@/components/nav/SubNav.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { useWorkspaceStore } from '@/stores/workspace.store'
import { useFillRunStore } from '@/stores/fillRun.store'

const workspace = useWorkspaceStore()
const fill = useFillRunStore()
const status = ref('')
const workspaceId = computed(() => workspace.currentWorkspace?.id || workspace.currentWorkspaceId)
const doneStatuses = ['succeeded', 'completed_with_failures']

async function load() {
  if (!workspace.workspaces.length) await workspace.load()
  if (workspaceId.value) await fill.loadRuns(workspaceId.value, status.value || undefined)
}

function percent(run: { progress_done: number; progress_total: number; status: string }) {
  if (run.progress_total > 0) return Math.round((run.progress_done / run.progress_total) * 100)
  return doneStatuses.includes(run.status) ? 100 : 0
}

onMounted(load)
</script>

<template>
  <SubNav title="我的填写任务">
    <el-select v-model="status" clearable placeholder="全部状态" style="width: 180px" @change="load">
      <el-option label="进行中" value="running" />
      <el-option label="排队中" value="queued" />
      <el-option label="已完成" value="succeeded" />
      <el-option label="失败" value="failed" />
      <el-option label="已取消" value="canceled" />
    </el-select>
  </SubNav>
  <main class="gk-shell gk-main history">
    <div v-if="fill.runs.length === 0" class="history__empty gk-card">暂无任务</div>
    <RouterLink v-for="run in fill.runs" :key="run.id" class="history__card gk-card" :to="{ name: 'fill-run-detail', params: { runId: run.id } }">
      <div>
        <div class="history__title">{{ run.name || run.target_namespace }}</div>
        <div class="gk-caption">{{ run.target_namespace }} · {{ dayjs(run.created_at).format('YYYY-MM-DD HH:mm') }} · {{ run.rows }}</div>
      </div>
      <StatusPill :status="run.status" />
      <el-progress :percentage="percent(run)" />
    </RouterLink>
  </main>
</template>

<style scoped>
.history {
  display: grid;
  gap: 14px;
}

.history__empty,
.history__card {
  padding: 20px;
}

.history__card {
  color: inherit;
  text-decoration: none;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 18px;
}

.history__title {
  font-size: 20px;
  font-weight: 600;
}
</style>
