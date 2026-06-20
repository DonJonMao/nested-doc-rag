import { fetchEventSource } from '@microsoft/fetch-event-source'
import { API_BASE } from './http'
import { useAuthStore } from '@/stores/auth.store'
import type { RunEvent } from './types'

export function subscribeRunEvents(args: {
  runId: string
  workspaceId: string
  afterSequence?: number
  onEvent: (event: RunEvent) => void
  onError?: (error: unknown) => void
  signal?: AbortSignal
}) {
  const auth = useAuthStore()
  const url = new URL(`${API_BASE}/api/v1/runs/${args.runId}/events`)
  url.searchParams.set('workspace_id', args.workspaceId)
  if (args.afterSequence) {
    url.searchParams.set('after_sequence', String(args.afterSequence))
  }
  return fetchEventSource(url.toString(), {
    method: 'GET',
    headers: { Authorization: `Bearer ${auth.accessToken}` },
    signal: args.signal,
    onmessage(message) {
      if (!message.data) return
      args.onEvent(JSON.parse(message.data) as RunEvent)
    },
    onerror(error) {
      args.onError?.(error)
      return 2000
    },
  })
}
