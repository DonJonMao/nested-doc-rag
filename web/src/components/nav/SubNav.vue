<script setup lang="ts">
defineProps<{ title: string; subtitle?: string }>()
</script>

<template>
  <div class="subnav">
    <div class="subnav__inner">
      <div>
        <div class="subnav__title">{{ title }}</div>
        <div v-if="subtitle" class="subnav__subtitle">{{ subtitle }}</div>
      </div>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.subnav {
  box-sizing: border-box;
  height: var(--gk-subnav-height);
  background: rgba(245, 245, 247, 0.72);
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  backdrop-filter: saturate(180%) blur(20px);
}

.subnav__inner {
  width: min(var(--gk-content-max), calc(100vw - 48px));
  height: 100%;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}

.subnav__title {
  font-size: 17px;
  font-weight: 600;
  color: var(--gk-ink);
}

.subnav__subtitle {
  font-size: 12px;
  color: var(--gk-ink-3);
}
</style>
