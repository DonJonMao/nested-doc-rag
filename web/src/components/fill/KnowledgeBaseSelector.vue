<script setup lang="ts">
import StatusPill from '@/components/common/StatusPill.vue'
import type { KnowledgeBase } from '@/api/types'

defineProps<{ items: KnowledgeBase[]; modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [id: string] }>()
</script>

<template>
  <div class="kb-selector">
    <button
      v-for="item in items"
      :key="item.id"
      class="kb-selector__item"
      :class="{ active: item.id === modelValue }"
      type="button"
      :disabled="item.status !== 'ready'"
      @click="emit('update:modelValue', item.id)"
    >
      <span class="kb-selector__name">{{ item.name }}</span>
      <StatusPill :status="item.status" />
      <span class="kb-selector__meta">{{ item.namespace }} · {{ item.document_count }} 份文档</span>
    </button>
  </div>
</template>

<style scoped>
.kb-selector {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.kb-selector__item {
  min-height: 116px;
  padding: 16px;
  border: 1px solid var(--gk-glass-line);
  border-radius: var(--gk-radius-lg);
  background: rgba(255, 255, 255, 0.56);
  text-align: left;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.66), 0 8px 22px rgba(31, 38, 52, 0.06);
  transition: border-color 160ms var(--gk-ease), background-color 160ms var(--gk-ease), box-shadow 160ms var(--gk-ease), transform 160ms var(--gk-ease);
}

.kb-selector__item:hover:not(:disabled) {
  border-color: rgba(0, 102, 204, 0.24);
  background: rgba(255, 255, 255, 0.78);
}

.kb-selector__item:active:not(:disabled) {
  transform: scale(0.99);
}

.kb-selector__item.active {
  border-color: rgba(0, 102, 204, 0.42);
  background: linear-gradient(135deg, rgba(234, 244, 255, 0.84), rgba(255, 255, 255, 0.58));
  box-shadow: 0 0 0 2px rgba(0, 102, 204, 0.16) inset, 0 10px 24px rgba(0, 102, 204, 0.08);
}

.kb-selector__item:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.kb-selector__name {
  font-size: 18px;
  font-weight: 600;
}

.kb-selector__meta {
  font-size: 13px;
  color: var(--gk-ink-3);
}

@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .kb-selector__item {
    -webkit-backdrop-filter: saturate(170%) blur(16px);
    backdrop-filter: saturate(170%) blur(16px);
  }
}
</style>
