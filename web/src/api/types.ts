export interface ApiResponse<T> {
  code: string
  message?: string
  data: T
  request_id?: string
}

export interface User {
  id: string
  username: string
  display_name?: string
  email?: string
  status?: string
  roles: string[]
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  expires_at: string
  user: User
}

export interface MeResponse {
  user: User
}

export interface Workspace {
  id: string
  name: string
  description?: string
}

export type KnowledgeBaseStatus = 'empty' | 'building' | 'ready' | 'stale' | 'failed' | 'archived'

export interface KnowledgeBase {
  id: string
  workspace_id: string
  name: string
  namespace: string
  description?: string
  qdrant_collection?: string
  current_index_version_id?: string
  status: KnowledgeBaseStatus
  document_count: number
  last_ingested_at?: string
  updated_at: string
}

export interface KnowledgeDocument {
  id: string
  knowledge_base_id: string
  workspace_id: string
  file_id: string
  filename: string
  document_role: string
  namespace: string
  status: string
  created_by: string
  created_at: string
  updated_at: string
}

export interface IngestionJob {
  id: string
  workspace_id: string
  knowledge_base_id: string
  index_version_id?: string
  job_id?: string
  status: string
  progress: number
  document_count: number
  error_message?: string
  created_at: string
  updated_at: string
}

export interface FormFile {
  id: string
  workspace_id: string
  file_id: string
  filename: string
  created_by: string
  created_at: string
}

export interface FillRun {
  id: string
  workspace_id: string
  form_file_id: string
  job_id?: string
  name: string
  knowledge_base_id?: string
  index_version_id?: string
  target_namespace: string
  global_namespace: string
  room_context?: string
  rows: string
  retrieval_mode: string
  prompt_version: string
  status: string
  progress_total: number
  progress_done: number
  filled_form_artifact_id?: string
  error_message?: string
  created_by: string
  created_at: string
  updated_at: string
}

export interface RunArtifact {
  id: string
  workspace_id: string
  run_id: string
  artifact_type: string
  filename: string
  content_type?: string
  size_bytes?: number
  created_at: string
}

export interface ReviewCounts {
  pending: number
  approved: number
  rejected: number
  edited: number
  ignored: number
  total: number
}

export interface FillRunResult {
  run: FillRun
  artifacts: RunArtifact[]
  review_counts?: ReviewCounts
  downloads?: Record<string, string>
}

export interface RunEvent {
  run_id: string
  event_type: string
  sequence: number
  payload?: Record<string, unknown>
  created_at: string
}
