import { http, unwrap } from './http'
import type { LoginResponse, MeResponse } from './types'

export async function login(payload: { username: string; password: string }) {
  return unwrap<LoginResponse>(await http.post('/api/v1/auth/login', payload))
}

export async function refresh(refreshToken: string) {
  return unwrap<LoginResponse>(await http.post('/api/v1/auth/refresh', { refresh_token: refreshToken }))
}

export async function logout(refreshToken: string) {
  return unwrap(await http.post('/api/v1/auth/logout', { refresh_token: refreshToken }))
}

export async function getMe() {
  return unwrap<MeResponse>(await http.get('/api/v1/auth/me'))
}
