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
  padding: 32px 16px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.9), rgba(245, 248, 255, 0.74) 46%, rgba(243, 248, 246, 0.9)),
    linear-gradient(125deg, rgba(0, 102, 204, 0.15), rgba(79, 183, 216, 0.1) 38%, rgba(255, 179, 64, 0.1));
  background-attachment: fixed;
}
</style>
