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

export interface FillRunSummaryCounts {
  total_fields: number
  answered: number
  partial_clue: number
  not_found: number
  conflict_unresolved: number
  writeback_allowed: number
  review_required: number
  failed_fields: number
  confirmed: number
  uncertain: number
  flagged: number
  written: number
}

export interface FillRunDownloads {
  filled_form_available: boolean
  review_items_available: boolean
  writeback_audit_available: boolean
}

export interface FillRunArtifactInfo {
  available: boolean
  filename?: string
  size?: number
}

export interface FillRunArtifactDownloads {
  filled_form: FillRunArtifactInfo
  review_items: FillRunArtifactInfo
  review_items_csv: FillRunArtifactInfo
  writeback_audit: FillRunArtifactInfo
  summary: FillRunArtifactInfo
}

export interface FillRunEvidenceRef {
  chunk_id?: string
  document_id?: string
  object_key?: string
  object_version_id?: string
  qdrant_point_id?: string
  source_type?: string
  source_anchor?: string
  page?: number | string | null
  sheet_name?: string
  cell?: string
  image_object_key?: string
  bbox?: unknown
  caption?: string
  file_name?: string
  text_preview?: string
}

export interface FillRunWritebackField {
  field_key?: string
  field_id?: string
  row_index?: number
  target_cell?: string
  sheet_name?: string
  cell?: string
  status?: 'confirmed' | 'uncertain' | 'flagged' | string
  answer_status?: string
  answer_value?: unknown
  writeback_action?: string
  evidence_refs: FillRunEvidenceRef[]
  error_code?: string
}

export interface FillRunWritebackBlock {
  summary: {
    confirmed: number
    uncertain: number
    flagged: number
    written: number
    review: number
  }
  fields: FillRunWritebackField[]
}

export interface FillRunListItem {
  id: string
  workspace_id: string
  name?: string
  raw_status?: string
  status: string
  created_at: string
  updated_at: string
  completed_at?: string
  template_file_name?: string
  kb_name?: string
  summary: FillRunSummaryCounts
  downloads: FillRunDownloads
}

export interface FillRunDetail {
  id: string
  workspace_id: string
  name?: string
  raw_status?: string
  status: string
  created_at: string
  updated_at: string
  completed_at?: string
  template_file_name?: string
  kb_name?: string
  manifest_status: 'valid' | 'invalid' | 'missing'
  artifact_validation_status: 'valid' | 'invalid' | 'missing'
  message: string
  error_message?: string
  summary: FillRunSummaryCounts
  artifacts: FillRunArtifactDownloads
  writeback?: FillRunWritebackBlock
  artifact_validation_warnings?: string[]
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
