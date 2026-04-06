import type { PaginatedResponse } from './common.js'

export type DocumentStatus = 'active' | 'inactive' | 'archived'

export interface DocumentMetadata {
  source_system: string | null
  warnings: string[]
  custom: Record<string, unknown>
}

export interface TermReference {
  field_path: string
  term_id: string
  terminology_ref?: string
  matched_via?: string
}

export interface Reference {
  field_path: string
  reference_type: 'document' | 'term' | 'terminology' | 'template'
  lookup_value: string
  version_strategy?: 'latest' | 'pinned'
  resolved: {
    document_id?: string
    identity_hash?: string
    template_id?: string
    version?: number
    term_id?: string
    terminology_value?: string
    matched_via?: string
    terminology_id?: string
    template_value?: string
  }
}

export interface Document {
  document_id: string
  namespace: string
  template_id: string
  template_value?: string
  template_version: number
  identity_hash: string
  version: number
  data: Record<string, unknown>
  term_references: TermReference[]
  references: Reference[]
  file_references: Array<Record<string, unknown>>
  status: DocumentStatus
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
  metadata: DocumentMetadata
  is_latest_version?: boolean
  latest_version?: number
}

export interface CreateDocumentRequest {
  template_id: string
  template_version?: number
  document_id?: string
  version?: number
  namespace: string
  data: Record<string, unknown>
  created_by?: string
  metadata?: Record<string, unknown>
  synonyms?: Array<Record<string, unknown>>
}

export interface DocumentCreateResponse {
  document_id: string
  namespace: string
  template_id: string
  template_value?: string
  identity_hash: string
  version: number
  is_new: boolean
  previous_version?: number
  warnings: string[]
}

export interface DocumentQueryParams {
  page?: number
  page_size?: number
  template_id?: string
  template_value?: string
  status?: DocumentStatus
  latest_only?: boolean
  cursor?: string
  namespace?: string
}

export interface DocumentListResponse extends PaginatedResponse<Document> {
  next_cursor?: string
}

export type QueryFilterOperator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'in' | 'nin' | 'exists' | 'regex'

export interface QueryFilter {
  /** Field path to filter on (e.g., 'data.account', 'data.status', 'template_id') */
  field: string
  /** Comparison operator. Default: 'eq' */
  operator?: QueryFilterOperator
  /** Value to compare against */
  value: unknown
}

export interface DocumentQueryRequest {
  /** Filter conditions (AND logic) */
  filters?: QueryFilter[]
  /** Filter by template ID */
  template_id?: string
  /** Filter by status */
  status?: DocumentStatus
  page?: number
  page_size?: number
  /** Field to sort by. Default: 'created_at' */
  sort_by?: string
  /** Sort order. Default: 'desc' */
  sort_order?: 'asc' | 'desc'
}

export interface DocumentValidationResponse {
  valid: boolean
  errors: Array<{
    field: string | null
    code: string
    message: string
    details?: Record<string, unknown>
  }>
  warnings: string[]
  identity_hash: string | null
  template_version: number | null
  term_references?: Array<Record<string, unknown>>
  references?: Array<Record<string, unknown>>
  file_references?: Array<Record<string, unknown>>
}

export interface ValidateDocumentRequest {
  template_id: string
  namespace: string
  data: Record<string, unknown>
}

export interface DocumentVersionSummary {
  document_id: string
  version: number
  status: DocumentStatus
  created_at: string
  created_by: string | null
}

export interface DocumentVersionResponse {
  identity_hash: string
  current_version: number
  versions: DocumentVersionSummary[]
}

export interface TableColumn {
  name: string
  label: string
  type: string
  is_array: boolean
  is_flattened: boolean
}

export interface TableViewResponse {
  template_id: string
  template_value: string
  template_label: string
  columns: TableColumn[]
  rows: Record<string, unknown>[]
  total_documents: number
  total_rows: number
  page: number
  page_size: number
  pages: number
  array_handling: 'none' | 'flattened' | 'json'
}

export interface TableViewParams {
  status?: DocumentStatus
  page?: number
  page_size?: number
  max_cross_product?: number
}

// ---- Import ----

export interface ImportPreviewResponse {
  headers: string[]
  rows: Record<string, unknown>[]
  format: string
  error?: string
}

export interface ImportDocumentsOptions {
  template_id: string
  column_mapping: Record<string, string>
  namespace: string
  skip_errors?: boolean
}

export interface ImportDocumentResult {
  row: number
  document_id: string
  version: number
  is_new: boolean
}

export interface ImportDocumentError {
  row: number
  error: string
  data: Record<string, string>
}

export interface ImportDocumentsResponse {
  total_rows: number
  succeeded: number
  failed: number
  skipped: number
  results: ImportDocumentResult[]
  errors: ImportDocumentError[]
}

// ---- Replay ----

export type ReplayStatus = 'pending' | 'running' | 'paused' | 'completed' | 'cancelled' | 'failed'

export interface ReplayFilter {
  template_id?: string
  template_value?: string
  namespace?: string
  status?: string
}

export interface ReplayRequest {
  filter?: ReplayFilter
  throttle_ms?: number
  batch_size?: number
}

export interface ReplaySessionResponse {
  session_id: string
  status: ReplayStatus
  total_count: number
  published: number
  throttle_ms: number
  message: string
}
