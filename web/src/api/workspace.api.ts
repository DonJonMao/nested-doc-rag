import { http, unwrap } from './http'
import type { Workspace } from './types'

export async function listWorkspaces() {
  return unwrap<Workspace[]>(await http.get('/api/v1/workspaces'))
}
