<template>
  <main class="not-found">
    <section class="gk-card">
      <h1>页面不存在</h1>
      <RouterLink :to="{ name: 'fill-create' }">返回自动填表</RouterLink>
    </section>
  </main>
</template>

<style scoped>
.not-found {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.88), rgba(245, 248, 255, 0.78)),
    linear-gradient(120deg, rgba(0, 102, 204, 0.12), rgba(255, 179, 64, 0.08));
}

.not-found section {
  padding: 32px;
  box-shadow: var(--gk-glass-shadow);
}
</style>
