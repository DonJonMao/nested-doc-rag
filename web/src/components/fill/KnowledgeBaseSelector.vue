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
  border: 1px solid var(--gk-hairline-soft);
  border-radius: var(--gk-radius-lg);
  background: #fff;
  text-align: left;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
  cursor: pointer;
}

.kb-selector__item.active {
  border-color: var(--gk-blue);
  box-shadow: 0 0 0 2px #d8ebff inset;
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
</style>
