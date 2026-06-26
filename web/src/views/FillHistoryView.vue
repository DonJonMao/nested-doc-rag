<script setup lang="ts">
import { onMounted, ref } from 'vue'
import dayjs from 'dayjs'
import SubNav from '@/components/nav/SubNav.vue'
import StatusPill from '@/components/common/StatusPill.vue'
import { useFillRunStore } from '@/stores/fillRun.store'

const fill = useFillRunStore()
const status = ref('')

async function load() {
  await fill.loadRuns(status.value || undefined)
}

function formatTime(value?: string) {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'
}

function shortId(value: string) {
  return value.slice(0, 8)
}

function count(value?: number) {
  return value ?? 0
}

onMounted(load)
</script>

<template>
  <SubNav title="我的填写任务">
    <el-select v-model="status" clearable placeholder="全部状态" style="width: 180px" @change="load">
      <el-option label="进行中" value="running" />
      <el-option label="排队中" value="queued" />
      <el-option label="已完成" value="succeeded" />
      <el-option label="部分失败" value="completed_with_failures" />
      <el-option label="失败" value="failed" />
      <el-option label="已取消" value="canceled" />
    </el-select>
  </SubNav>
  <main class="gk-shell gk-main history">
    <section class="history__table gk-card">
      <el-table :data="fill.runs" v-loading="fill.loading" empty-text="暂无任务">
        <el-table-column label="任务 ID" min-width="120">
          <template #default="{ row }">
            <span class="history__id" :title="row.id">{{ shortId(row.id) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="任务名称" min-width="180">
          <template #default="{ row }">
            <div class="history__name">{{ row.name || row.template_file_name || row.id }}</div>
            <div class="gk-caption">{{ row.kb_name || '未关联知识库' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <StatusPill :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="150">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="完成时间" min-width="150">
          <template #default="{ row }">{{ formatTime(row.completed_at) }}</template>
        </el-table-column>
        <el-table-column label="总字段" width="90" align="right">
          <template #default="{ row }">{{ row.summary.total_fields }}</template>
        </el-table-column>
        <el-table-column label="自动写入" width="100" align="right">
          <template #default="{ row }">{{ count(row.summary.written ?? row.summary.writeback_allowed) }}</template>
        </el-table-column>
        <el-table-column label="确认/存疑/标记" width="150" align="right">
          <template #default="{ row }">
            {{ count(row.summary.confirmed) }}/{{ count(row.summary.uncertain) }}/{{ count(row.summary.flagged) }}
          </template>
        </el-table-column>
        <el-table-column label="需人工补充/复核" width="150" align="right">
          <template #default="{ row }">{{ row.summary.review_required }}</template>
        </el-table-column>
        <el-table-column label="失败字段" width="100" align="right">
          <template #default="{ row }">{{ row.summary.failed_fields }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <RouterLink :to="{ name: 'fill-run-detail', params: { runId: row.id } }">
              <el-button link type="primary">查看详情</el-button>
            </RouterLink>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </main>
</template>

<style scoped>
.history {
  display: grid;
  gap: 14px;
}

.history__table {
  padding: 20px;
  box-shadow: var(--gk-glass-shadow);
}

.history__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: var(--gk-ink-2);
}

.history__name {
  font-weight: 600;
  color: var(--gk-ink-1);
}

.history__table :deep(.el-table) {
  background: rgba(255, 255, 255, 0.12);
}

.history__table :deep(.el-table__row) {
  transition: background-color 160ms var(--gk-ease);
}
</style>
