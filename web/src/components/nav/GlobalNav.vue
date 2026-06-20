<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LogOut } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth.store'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const items = computed(() => [
  { label: '自动填表', name: 'fill-create', visible: true },
  { label: '我的填表任务', name: 'fill-history', visible: true },
  { label: '知识库管理', name: 'admin-knowledge', visible: auth.isAdmin },
])

async function signOut() {
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header class="global-nav">
    <div class="global-nav__inner">
      <div class="global-nav__brand">工勘智能填表</div>
      <nav class="global-nav__links">
        <RouterLink v-for="item in items.filter((entry) => entry.visible)" :key="item.name" :to="{ name: item.name }" :class="{ active: route.name === item.name }">
          {{ item.label }}
        </RouterLink>
      </nav>
      <button class="global-nav__logout" type="button" title="退出登录" @click="signOut">
        <LogOut :size="16" />
      </button>
    </div>
  </header>
</template>

<style scoped>
.global-nav {
  height: var(--gk-nav-height);
  background: var(--gk-black);
  color: rgba(255, 255, 255, 0.82);
}

.global-nav__inner {
  width: min(var(--gk-content-max), calc(100vw - 48px));
  height: 100%;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 28px;
}

.global-nav__brand {
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

.global-nav__links {
  display: flex;
  align-items: center;
  gap: 22px;
  flex: 1;
}

.global-nav__links a {
  color: rgba(255, 255, 255, 0.68);
  font-size: 13px;
  text-decoration: none;
}

.global-nav__links a.active,
.global-nav__links a:hover {
  color: #fff;
}

.global-nav__logout {
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: var(--gk-radius-pill);
  color: rgba(255, 255, 255, 0.78);
  background: transparent;
  display: grid;
  place-items: center;
  cursor: pointer;
}

.global-nav__logout:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.12);
}
</style>
