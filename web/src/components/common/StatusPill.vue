<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const toneMap: Record<string, string> = {
  ready: 'success',
  completed: 'success',
  succeeded: 'success',
  indexed: 'success',
  running: 'info',
  queued: 'info',
  building: 'info',
  uploaded: 'info',
  stale: 'warning',
  completed_with_failures: 'warning',
  cancel_requested: 'warning',
  failed: 'danger',
  canceled: 'muted',
  cancelled: 'muted',
  empty: 'muted',
}

const labelMap: Record<string, string> = {
  ready: '就绪',
  completed: '已完成',
  succeeded: '已完成',
  indexed: '已入库',
  running: '运行中',
  queued: '排队中',
  building: '入库中',
  uploaded: '已上传',
  stale: '需更新',
  completed_with_failures: '部分失败',
  cancel_requested: '取消中',
  failed: '失败',
  canceled: '已取消',
  cancelled: '已取消',
  empty: '空',
}

const label = computed(() => labelMap[props.status] || props.status)
</script>

<template>
  <span class="status-pill" :class="`status-pill--${toneMap[props.status] || 'muted'}`">{{ label }}</span>
</template>

<style scoped>
.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0 10px;
  border-radius: var(--gk-radius-pill);
  font-size: 12px;
  font-weight: 600;
  border: 1px solid transparent;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.64);
  white-space: nowrap;
}

.status-pill--success {
  color: var(--gk-success);
  background: rgba(237, 248, 240, 0.78);
  border-color: rgba(202, 233, 210, 0.9);
}

.status-pill--info {
  color: var(--gk-info);
  background: rgba(234, 244, 255, 0.78);
  border-color: rgba(201, 226, 255, 0.9);
}

.status-pill--warning {
  color: var(--gk-warning);
  background: rgba(255, 247, 232, 0.78);
  border-color: rgba(241, 213, 168, 0.9);
}

.status-pill--danger {
  color: var(--gk-danger);
  background: rgba(255, 240, 239, 0.8);
  border-color: rgba(240, 200, 197, 0.9);
}

.status-pill--muted {
  color: var(--gk-ink-3);
  background: rgba(241, 241, 244, 0.76);
  border-color: rgba(88, 100, 120, 0.12);
}
</style>
