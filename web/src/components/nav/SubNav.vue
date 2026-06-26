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
  position: sticky;
  top: var(--gk-nav-height);
  z-index: 50;
  box-sizing: border-box;
  height: var(--gk-subnav-height);
  background: rgba(255, 255, 255, 0.9);
  border-bottom: 1px solid rgba(88, 100, 120, 0.12);
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.62) inset;
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

@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .subnav {
    background: rgba(255, 255, 255, 0.56);
    -webkit-backdrop-filter: saturate(var(--gk-glass-saturate)) blur(22px);
    backdrop-filter: saturate(var(--gk-glass-saturate)) blur(22px);
  }
}

@media (max-width: 720px) {
  .subnav__inner {
    width: min(100% - 24px, var(--gk-content-max));
    gap: 12px;
  }

  .subnav__subtitle {
    display: none;
  }
}
</style>
