<script setup lang="ts">
import { Download } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { downloadWithAuth } from '@/api/http'
import type { FillRun } from '@/api/types'

const props = defineProps<{ run: FillRun }>()

const links = [
  { label: '填好的工勘单', kind: 'filled-form', filename: 'filled_form.xlsx', primary: true },
  { label: '运行摘要', kind: 'run-summary', filename: 'run_summary.md' },
  { label: '审核项', kind: 'review-items', filename: 'review_items.jsonl' },
  { label: 'Trace', kind: 'trace', filename: 'trace.jsonl' },
]

async function download(kind: string, filename: string) {
  try {
    await downloadWithAuth(`/api/v1/fill-runs/${props.run.id}/download/${kind}`, filename)
  } catch {
    ElMessage.error('下载失败')
  }
}
</script>

<template>
  <section class="download-panel gk-card">
    <h2 class="gk-card-title">结果下载</h2>
    <p class="gk-caption">任务完成后可下载回填 Excel 和审计产物。</p>
    <div class="download-panel__actions">
      <el-button
        v-for="link in links"
        :key="link.kind"
        :type="link.primary ? 'primary' : 'default'"
        :disabled="!['succeeded', 'completed_with_failures'].includes(run.status)"
        @click="download(link.kind, link.filename)"
      >
        <Download :size="16" />
        {{ link.label }}
      </el-button>
    </div>
  </section>
</template>

<style scoped>
.download-panel {
  padding: 24px;
}

.download-panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px;
}
</style>
