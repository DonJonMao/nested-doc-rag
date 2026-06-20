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

export async function downloadWithAuth(url: string, fallbackFilename: string) {
  const auth = useAuthStore()
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { Authorization: `Bearer ${auth.accessToken}` },
  })
  if (!res.ok) {
    throw new Error(await downloadErrorMessage(res))
  }
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filenameFromDisposition(res.headers.get('Content-Disposition')) || fallbackFilename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}

function filenameFromDisposition(disposition: string | null) {
  if (!disposition) return ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) return decodeURIComponent(encoded.replace(/"/g, ''))
  const plain = disposition.match(/filename="([^"]+)"/i)?.[1] || disposition.match(/filename=([^;]+)/i)?.[1]
  return plain ? plain.trim().replace(/^"|"$/g, '') : ''
}

async function downloadErrorMessage(res: Response) {
  let message = ''
  try {
    const payload = await res.clone().json()
    message = payload?.message || ''
  } catch {
    message = ''
  }
  if (message) return message
  if (res.status === 403) return '无权下载该文件'
  if (res.status === 404) return '结果文件不存在'
  if (res.status === 409) return '任务尚未完成或结果校验失败'
  return `下载失败：${res.status}`
}
