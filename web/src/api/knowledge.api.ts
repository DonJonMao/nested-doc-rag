import { http, unwrap } from './http'
import type { IngestionJob, KnowledgeBase, KnowledgeDocument } from './types'

export async function listKnowledgeOptions(workspaceId: string) {
  const data = unwrap<{ knowledge_bases: KnowledgeBase[] }>(
    await http.get('/api/v1/knowledge-bases/options', { params: { workspace_id: workspaceId } }),
  )
  return data.knowledge_bases
}

export async function listKnowledgeDocuments(kbId: string) {
  const data = unwrap<{ documents: KnowledgeDocument[] }>(await http.get(`/api/v1/knowledge-bases/${kbId}/documents`))
  return data.documents
}

export async function uploadKnowledgeDocument(kbId: string, file: File, autoIngest = true) {
  const form = new FormData()
  form.append('document_role', 'knowledge_base')
  form.append('file', file)
  return unwrap<KnowledgeDocument | { document: KnowledgeDocument; ingestion_job?: IngestionJob }>(
    await http.post(`/api/v1/knowledge-bases/${kbId}/documents`, form, { params: { auto_ingest: autoIngest } }),
  )
}

export async function deleteKnowledgeDocument(docId: string, reindex = true) {
  return unwrap<{ document: KnowledgeDocument; ingestion_job?: IngestionJob; deleted: boolean }>(
    await http.delete(`/api/v1/documents/${docId}`, { params: { reindex } }),
  )
}
