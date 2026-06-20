<template>
  <main class="auth-layout">
    <slot />
  </main>
</template>

<style scoped>
.auth-layout {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: var(--gk-page);
}
</style>
