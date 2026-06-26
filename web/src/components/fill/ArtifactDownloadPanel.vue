<script setup lang="ts">
import { computed } from 'vue'
import { Download, FileSpreadsheet, FileText } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { downloadFilledForm, downloadReviewItems, downloadSummary, downloadWritebackAudit } from '@/api/fillRuns.api'
import type { FillRunDetail } from '@/api/types'

const props = defineProps<{ run: FillRunDetail }>()

const terminal = computed(() => ['completed', 'succeeded', 'completed_with_failures'].includes(props.run.status))
const filledFormBlocked = computed(() => !terminal.value || props.run.artifact_validation_status !== 'valid' || !props.run.artifacts.filled_form.available)

async function runDownload(action: () => Promise<void>) {
  try {
    await action()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '下载失败')
  }
}
</script>

<template>
  <section class="download-panel gk-card">
    <h2 class="gk-card-title">结果下载</h2>
    <p class="gk-caption">下载的是安全自动填写版；未写入字段需在表格下载后线下人工补充。</p>
    <div class="download-panel__actions">
      <el-button
        type="primary"
        :disabled="filledFormBlocked"
        @click="runDownload(() => downloadFilledForm(run.id))"
      >
        <FileSpreadsheet :size="16" />
        下载自动填写后的表格
      </el-button>
      <el-button
        :disabled="!terminal || !run.artifacts.review_items_csv.available"
        @click="runDownload(() => downloadReviewItems(run.id, 'csv'))"
      >
        <Download :size="16" />
        下载需人工补充字段清单
      </el-button>
      <el-button
        :disabled="!terminal || !run.artifacts.writeback_audit.available"
        @click="runDownload(() => downloadWritebackAudit(run.id))"
      >
        <FileText :size="16" />
        下载写回审计
      </el-button>
      <el-button
        :disabled="!terminal || !run.artifacts.summary.available"
        @click="runDownload(() => downloadSummary(run.id))"
      >
        <FileText :size="16" />
        下载摘要
      </el-button>
    </div>
  </section>
</template>

<style scoped>
.download-panel {
  padding: 24px;
  box-shadow: var(--gk-glass-shadow);
}

.download-panel__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px;
}
</style>
