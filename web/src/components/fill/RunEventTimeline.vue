<script setup lang="ts">
import { computed } from 'vue'
import dayjs from 'dayjs'
import type { RunEvent } from '@/api/types'

const props = defineProps<{ events: RunEvent[] }>()

const visibleEvents = computed(() => props.events.filter((event) => event.event_type !== 'heartbeat'))

function title(event: RunEvent) {
  if (event.event_type === 'progress') return '行处理完成'
  return event.event_type
}

function message(event: RunEvent) {
  return String(event.payload?.message || event.payload?.status || event.event_type)
}
</script>

<template>
  <div class="timeline gk-card">
    <h2 class="gk-card-title">运行事件</h2>
    <div v-if="visibleEvents.length === 0" class="timeline__empty">暂无事件</div>
    <div v-else class="timeline__list">
      <div v-for="event in visibleEvents" :key="`${event.run_id}-${event.sequence}`" class="timeline__item">
        <div class="timeline__dot" />
        <div>
          <div class="timeline__title">{{ title(event) }}</div>
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
  box-shadow: var(--gk-glass-shadow-soft);
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
  padding: 10px 0;
  border-bottom: 1px solid var(--gk-glass-line);
}

.timeline__item:last-child {
  border-bottom: 0;
}

.timeline__dot {
  width: 8px;
  height: 8px;
  margin-top: 7px;
  border-radius: 50%;
  background: linear-gradient(180deg, var(--gk-cyan), var(--gk-blue));
  box-shadow: 0 0 0 4px rgba(0, 102, 204, 0.1);
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
