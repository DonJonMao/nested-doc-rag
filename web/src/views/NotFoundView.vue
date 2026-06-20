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
  background: var(--gk-page);
}

.not-found section {
  padding: 32px;
}
</style>
