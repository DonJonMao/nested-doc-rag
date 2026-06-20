import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import * as authApi from '@/api/auth.api'
import type { User } from '@/api/types'

const ACCESS_KEY = 'gongkan.access_token'
const REFRESH_KEY = 'gongkan.refresh_token'

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref(sessionStorage.getItem(ACCESS_KEY) || '')
  const refreshToken = ref(localStorage.getItem(REFRESH_KEY) || '')
  const user = ref<User | null>(null)
  const loading = ref(false)

  const isAdmin = computed(() => user.value?.roles.includes('admin') ?? false)

  function persist() {
    if (accessToken.value) sessionStorage.setItem(ACCESS_KEY, accessToken.value)
    else sessionStorage.removeItem(ACCESS_KEY)
    if (refreshToken.value) localStorage.setItem(REFRESH_KEY, refreshToken.value)
    else localStorage.removeItem(REFRESH_KEY)
  }

  async function login(username: string, password: string) {
    loading.value = true
    try {
      const result = await authApi.login({ username, password })
      accessToken.value = result.access_token
      refreshToken.value = result.refresh_token
      user.value = result.user
      persist()
      return result.user
    } finally {
      loading.value = false
    }
  }

  async function refresh() {
    if (!refreshToken.value) throw new Error('missing refresh token')
    const result = await authApi.refresh(refreshToken.value)
    accessToken.value = result.access_token
    refreshToken.value = result.refresh_token
    user.value = result.user
    persist()
  }

  async function fetchMe() {
    const result = await authApi.getMe()
    user.value = result.user
  }

  async function logout() {
    if (refreshToken.value) {
      await authApi.logout(refreshToken.value).catch(() => undefined)
    }
    accessToken.value = ''
    refreshToken.value = ''
    user.value = null
    persist()
  }

  return { accessToken, refreshToken, user, loading, isAdmin, login, refresh, fetchMe, logout }
})
