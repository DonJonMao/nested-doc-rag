import axios from 'axios'
import { useAuthStore } from '@/stores/auth.store'
import type { ApiResponse } from './types'

export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080'

export const http = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.accessToken) {
    config.headers.Authorization = `Bearer ${auth.accessToken}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const auth = useAuthStore()
    const status = error.response?.status
    const original = error.config
    if (status === 401 && !original.__retried && auth.refreshToken) {
      original.__retried = true
      await auth.refresh()
      original.headers.Authorization = `Bearer ${auth.accessToken}`
      return http(original)
    }
    return Promise.reject(error)
  },
)

export function unwrap<T>(response: unknown): T {
  if (response && typeof response === 'object' && 'data' in response && 'code' in response) {
    return (response as ApiResponse<T>).data
  }
  return response as T
}

export async function downloadWithAuth(url: string, filename: string) {
  const auth = useAuthStore()
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { Authorization: `Bearer ${auth.accessToken}` },
  })
  if (!res.ok) {
    throw new Error(`download failed: ${res.status}`)
  }
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}
