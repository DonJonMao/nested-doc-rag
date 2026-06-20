import { downloadWithAuth, http, unwrap } from './http'
import type { FillRun, FillRunDetail, FillRunListItem, RunArtifact } from './types'

export async function createSimpleFillRun(payload: {
  workspace_id: string
  knowledge_base_id: string
  form_file_id: string
  name?: string
  room_context?: string
}) {
  return unwrap<FillRun>(await http.post('/api/v1/fill-runs/simple', payload))
}

export async function listFillRuns(status?: string) {
  const data = unwrap<{ fill_runs: FillRunListItem[] }>(
    await http.get('/api/v1/fill-runs', { params: { status } }),
  )
  return data.fill_runs
}

export async function getFillRun(runId: string) {
  return unwrap<FillRunDetail>(await http.get(`/api/v1/fill-runs/${runId}`))
}

export async function cancelFillRun(runId: string) {
  return unwrap<{ fill_run: FillRun; canceled: boolean }>(await http.post(`/api/v1/fill-runs/${runId}/cancel`))
}

export async function listFillRunArtifacts(runId: string) {
  const data = unwrap<{ artifacts: RunArtifact[] }>(await http.get(`/api/v1/fill-runs/${runId}/artifacts`))
  return data.artifacts
}

export async function downloadFilledForm(runId: string) {
  return downloadWithAuth(`/api/v1/fill-runs/${runId}/downloads/filled-form`, 'filled_form.xlsx')
}

export async function downloadReviewItems(runId: string, format: 'csv' | 'jsonl' = 'csv') {
  const suffix = format === 'csv' ? 'csv' : 'jsonl'
  return downloadWithAuth(`/api/v1/fill-runs/${runId}/downloads/review-items?format=${format}`, `review_items.${suffix}`)
}

export async function downloadWritebackAudit(runId: string) {
  return downloadWithAuth(`/api/v1/fill-runs/${runId}/downloads/writeback-audit`, 'writeback_audit.jsonl')
}

export async function downloadSummary(runId: string) {
  return downloadWithAuth(`/api/v1/fill-runs/${runId}/downloads/summary`, 'summary.json')
}
