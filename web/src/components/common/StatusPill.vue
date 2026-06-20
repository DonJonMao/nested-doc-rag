<script setup lang="ts">
const props = defineProps<{ status: string }>()

const toneMap: Record<string, string> = {
  ready: 'success',
  succeeded: 'success',
  indexed: 'success',
  running: 'info',
  queued: 'info',
  building: 'info',
  uploaded: 'info',
  stale: 'warning',
  cancel_requested: 'warning',
  failed: 'danger',
  canceled: 'muted',
  empty: 'muted',
}
</script>

<template>
  <span class="status-pill" :class="`status-pill--${toneMap[props.status] || 'muted'}`">{{ props.status }}</span>
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
}

.status-pill--success {
  color: var(--gk-success);
  background: #edf8f0;
  border-color: #cae9d2;
}

.status-pill--info {
  color: var(--gk-info);
  background: #eaf4ff;
  border-color: #c9e2ff;
}

.status-pill--warning {
  color: var(--gk-warning);
  background: #fff7e8;
  border-color: #f1d5a8;
}

.status-pill--danger {
  color: var(--gk-danger);
  background: #fff0ef;
  border-color: #f0c8c5;
}

.status-pill--muted {
  color: var(--gk-ink-3);
  background: #f1f1f4;
  border-color: var(--gk-hairline-soft);
}
</style>
