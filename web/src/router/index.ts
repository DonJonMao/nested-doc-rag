import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth.store'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    {
      path: '/',
      component: () => import('@/layouts/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        { path: '', redirect: '/fill' },
        { path: 'fill', name: 'fill-create', component: () => import('@/views/FillCreateView.vue') },
        { path: 'fill-runs', name: 'fill-history', component: () => import('@/views/FillHistoryView.vue') },
        { path: 'fill-runs/:runId', name: 'fill-run-detail', component: () => import('@/views/FillRunDetailView.vue') },
        { path: 'fill/history', redirect: { name: 'fill-history' } },
        { path: 'fill/runs/:runId', redirect: (to) => ({ name: 'fill-run-detail', params: to.params }) },
        { path: 'admin/knowledge', name: 'admin-knowledge', component: () => import('@/views/AdminKnowledgeView.vue'), meta: { requiresAdmin: true } },
      ],
    },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue') },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.accessToken) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (auth.accessToken && !auth.user) {
    await auth.fetchMe().catch(() => auth.logout())
  }
  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return { name: 'fill-create' }
  }
  if (to.name === 'login' && auth.accessToken) {
    return { name: 'fill-create' }
  }
})
