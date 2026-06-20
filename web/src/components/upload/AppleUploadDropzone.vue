<script setup lang="ts">
import { ref } from 'vue'
import { UploadCloud } from 'lucide-vue-next'

defineProps<{ accept?: string; title?: string; subtitle?: string; busy?: boolean }>()
const emit = defineEmits<{ selected: [file: File] }>()
const input = ref<HTMLInputElement | null>(null)
const dragging = ref(false)

function choose() {
  input.value?.click()
}

function onFileList(files: FileList | null) {
  const file = files?.[0]
  if (file) emit('selected', file)
}
</script>

<template>
  <button
    class="dropzone"
    :class="{ 'dropzone--dragging': dragging }"
    type="button"
    :disabled="busy"
    @click="choose"
    @dragenter.prevent="dragging = true"
    @dragover.prevent="dragging = true"
    @dragleave.prevent="dragging = false"
    @drop.prevent="(event) => { dragging = false; onFileList(event.dataTransfer?.files || null) }"
  >
    <UploadCloud :size="34" />
    <span class="dropzone__title">{{ title || '选择或拖拽文件' }}</span>
    <span class="dropzone__subtitle">{{ subtitle || '支持后端允许的 Office 文件格式' }}</span>
    <input ref="input" class="dropzone__input" type="file" :accept="accept" @change="onFileList(($event.target as HTMLInputElement).files)" />
  </button>
</template>

<style scoped>
.dropzone {
  width: 100%;
  min-height: 214px;
  border: 1px dashed var(--gk-hairline);
  border-radius: var(--gk-radius-xl);
  background: var(--gk-page-soft);
  color: var(--gk-ink);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  cursor: pointer;
  transition: border-color 160ms var(--gk-ease), background-color 160ms var(--gk-ease), transform 160ms var(--gk-ease);
}

.dropzone:hover,
.dropzone--dragging {
  border-color: var(--gk-blue);
  background: #f0f7ff;
}

.dropzone:active {
  transform: scale(0.99);
}

.dropzone:disabled {
  cursor: wait;
  opacity: 0.7;
}

.dropzone__title {
  font-size: 21px;
  font-weight: 600;
}

.dropzone__subtitle {
  font-size: 14px;
  color: var(--gk-ink-3);
}

.dropzone__input {
  display: none;
}
</style>
