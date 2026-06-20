import { http, unwrap } from './http'
import type { FillRun, FillRunResult, RunArtifact } from './types'

export async function createSimpleFillRun(payload: {
  workspace_id: string
  knowledge_base_id: string
  form_file_id: string
  name?: string
  room_context?: string
}) {
  return unwrap<FillRun>(await http.post('/api/v1/fill-runs/simple', payload))
}

export async function listFillRuns(workspaceId: string, status?: string, mine = true) {
  const data = unwrap<{ fill_runs: FillRun[] }>(
    await http.get('/api/v1/fill-runs', { params: { workspace_id: workspaceId, status, mine } }),
  )
  return data.fill_runs
}

export async function getFillRun(runId: string) {
  return unwrap<FillRun>(await http.get(`/api/v1/fill-runs/${runId}`))
}

export async function getFillRunResult(runId: string) {
  return unwrap<FillRunResult>(await http.get(`/api/v1/fill-runs/${runId}/result`))
}

export async function cancelFillRun(runId: string) {
  return unwrap<{ fill_run: FillRun; canceled: boolean }>(await http.post(`/api/v1/fill-runs/${runId}/cancel`))
}

export async function listFillRunArtifacts(runId: string) {
  const data = unwrap<{ artifacts: RunArtifact[] }>(await http.get(`/api/v1/fill-runs/${runId}/artifacts`))
  return data.artifacts
}
