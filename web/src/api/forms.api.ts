import { http, unwrap } from './http'
import type { FormFile } from './types'

export async function uploadForm(workspaceId: string, file: File) {
  const form = new FormData()
  form.append('workspace_id', workspaceId)
  form.append('file', file)
  return unwrap<FormFile>(await http.post('/api/v1/forms', form))
}
