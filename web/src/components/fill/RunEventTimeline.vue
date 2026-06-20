<script setup lang="ts">
import dayjs from 'dayjs'
import type { RunEvent } from '@/api/types'

defineProps<{ events: RunEvent[] }>()

function message(event: RunEvent) {
  return String(event.payload?.message || event.payload?.status || event.event_type)
}
</script>

<template>
  <div class="timeline gk-card">
    <h2 class="gk-card-title">运行事件</h2>
    <div v-if="events.length === 0" class="timeline__empty">暂无事件</div>
    <div v-else class="timeline__list">
      <div v-for="event in events" :key="`${event.run_id}-${event.sequence}`" class="timeline__item">
        <div class="timeline__dot" />
        <div>
          <div class="timeline__title">{{ event.event_type }}</div>
          <div class="timeline__message">{{ message(event) }}</div>
          <div class="gk-caption">{{ dayjs(event.created_at).format('YYYY-MM-DD HH:mm:ss') }} · #{{ event.sequence }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.timeline {
  padding: 24px;
}

.timeline__empty {
  margin-top: 16px;
  color: var(--gk-ink-3);
}

.timeline__list {
  display: grid;
  gap: 18px;
  margin-top: 20px;
}

.timeline__item {
  display: grid;
  grid-template-columns: 14px 1fr;
  gap: 12px;
}

.timeline__dot {
  width: 8px;
  height: 8px;
  margin-top: 7px;
  border-radius: 50%;
  background: var(--gk-blue);
}

.timeline__title {
  font-size: 15px;
  font-weight: 600;
}

.timeline__message {
  margin: 3px 0;
  color: var(--gk-ink-2);
}
</style>
