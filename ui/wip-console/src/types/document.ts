// =============================================================================
// DOCUMENT TYPES
// =============================================================================

export type DocumentStatus = 'active' | 'inactive' | 'archived'

export interface DocumentMetadata {
  source_system: string | null
  warnings: string[]
  custom: Record<string, unknown>
}

// Term reference entry (array format for indexing)
export interface TermReference {
  field_path: string
  term_id: string
  terminology_ref?: string
  matched_via?: string
}

// Reference entry (array format for indexing)
export interface Reference {
  field_path: string
  reference_type: 'document' | 'term' | 'terminology' | 'template'
  lookup_value: string
  version_strategy?: 'latest' | 'pinned'
  resolved: {
    // For document references
    document_id?: string
    identity_hash?: string
    template_id?: string
    version?: number
    // For term references
    term_id?: string
    terminology_code?: string
    matched_via?: string
    // For terminology references
    terminology_id?: string
    // For template references
    template_code?: string
  }
}

export interface Document {
  document_id: string
  template_id: string
  template_version: number
  identity_hash: string
  version: number
  data: Record<string, unknown>
  term_references: TermReference[]  // Array format for indexing
  references: Reference[]  // Unified references
  status: DocumentStatus
  created_at: string
  created_by: string | null
  updated_at: string
  updated_by: string | null
  metadata: DocumentMetadata
  // Version tracking
  is_latest_version?: boolean
  latest_version?: number
  latest_document_id?: string
}

// =============================================================================
// REQUEST/RESPONSE TYPES
// =============================================================================

export interface CreateDocumentRequest {
  template_id: string
  data: Record<string, unknown>
  created_by?: string
  metadata?: {
    source_system?: string
    custom?: Record<string, unknown>
  }
}

export interface UpdateDocumentRequest {
  data?: Record<string, unknown>
  updated_by?: string
  metadata?: {
    source_system?: string
    custom?: Record<string, unknown>
  }
}

export interface DocumentListResponse {
  items: Document[]
  total: number
  page: number
  page_size: number
  pages: number
}

// =============================================================================
// VALIDATION TYPES
// =============================================================================

export interface DocumentValidationError {
  field: string | null
  code: string
  message: string
  details?: Record<string, unknown>
}

export interface DocumentValidationResponse {
  valid: boolean
  errors: DocumentValidationError[]
  warnings: string[]
  identity_hash: string | null
  template_version: number | null
}

export interface ValidateDocumentRequest {
  template_id: string
  data: Record<string, unknown>
}

// =============================================================================
// VERSION TYPES
// =============================================================================

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

// =============================================================================
// QUERY TYPES
// =============================================================================

export interface DocumentQueryParams {
  page?: number
  page_size?: number
  template_id?: string
  status?: DocumentStatus
  search?: string
}

// =============================================================================
// BULK OPERATION TYPES
// =============================================================================

export interface BulkCreateDocumentRequest {
  documents: CreateDocumentRequest[]
  created_by?: string
}

export interface DocumentBulkOperationResult {
  index: number
  status: 'created' | 'updated' | 'error' | 'skipped'
  document_id?: string
  identity_hash?: string
  error?: string
}

export interface DocumentBulkOperationResponse {
  results: DocumentBulkOperationResult[]
  total: number
  succeeded: number
  failed: number
}

// =============================================================================
// TABLE VIEW TYPES
// =============================================================================

export interface TableColumn {
  name: string
  label: string
  type: string
  is_array: boolean
  is_flattened: boolean
}

export interface TableViewResponse {
  template_id: string
  template_code: string
  template_name: string
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
