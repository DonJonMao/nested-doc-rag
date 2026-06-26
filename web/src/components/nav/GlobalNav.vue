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
  position: sticky;
  top: 0;
  z-index: 60;
  height: var(--gk-nav-height);
  color: var(--gk-ink-2);
  background: rgba(255, 255, 255, 0.94);
  border-bottom: 1px solid rgba(88, 100, 120, 0.12);
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.7) inset;
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
  color: var(--gk-ink);
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
  min-height: 28px;
  padding: 0 12px;
  border-radius: var(--gk-radius-pill);
  color: var(--gk-ink-3);
  font-size: 13px;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  transition: color 160ms var(--gk-ease), background-color 160ms var(--gk-ease), box-shadow 160ms var(--gk-ease);
}

.global-nav__links a.active,
.global-nav__links a:hover {
  color: var(--gk-ink);
  background: rgba(255, 255, 255, 0.72);
  box-shadow: inset 0 0 0 1px rgba(88, 100, 120, 0.1), 0 4px 12px rgba(31, 38, 52, 0.08);
}

.global-nav__logout {
  width: 30px;
  height: 30px;
  border: 1px solid rgba(88, 100, 120, 0.1);
  border-radius: var(--gk-radius-pill);
  color: var(--gk-ink-3);
  background: rgba(255, 255, 255, 0.54);
  display: grid;
  place-items: center;
  cursor: pointer;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.72);
}

.global-nav__logout:hover {
  color: var(--gk-ink);
  background: rgba(255, 255, 255, 0.86);
}

@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .global-nav {
    background: rgba(255, 255, 255, 0.62);
    -webkit-backdrop-filter: saturate(var(--gk-glass-saturate)) blur(var(--gk-glass-blur));
    backdrop-filter: saturate(var(--gk-glass-saturate)) blur(var(--gk-glass-blur));
  }

  .global-nav__links a.active,
  .global-nav__links a:hover,
  .global-nav__logout {
    -webkit-backdrop-filter: saturate(170%) blur(18px);
    backdrop-filter: saturate(170%) blur(18px);
  }
}

@media (max-width: 720px) {
  .global-nav__inner {
    width: min(100% - 24px, var(--gk-content-max));
    gap: 12px;
  }

  .global-nav__links {
    gap: 4px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .global-nav__links::-webkit-scrollbar {
    display: none;
  }

  .global-nav__brand {
    display: none;
  }
}
</style>
