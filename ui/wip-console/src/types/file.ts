// =============================================================================
// FILE TYPES
// =============================================================================

export type FileStatus = 'orphan' | 'active' | 'inactive'

export interface FileMetadata {
  description: string | null
  tags: string[]
  category: string | null
  custom: Record<string, unknown>
}

export interface FileEntity {
  file_id: string
  filename: string
  content_type: string
  size_bytes: number
  checksum: string
  storage_key: string
  metadata: FileMetadata
  status: FileStatus
  reference_count: number
  allowed_templates: string[] | null
  uploaded_at: string
  uploaded_by: string | null
  updated_at: string | null
  updated_by: string | null
}

// =============================================================================
// REQUEST/RESPONSE TYPES
// =============================================================================

export interface FileUploadMetadata {
  description?: string
  tags?: string[]
  category?: string
  custom?: Record<string, unknown>
  allowed_templates?: string[]
}

export interface UpdateFileMetadataRequest {
  description?: string
  tags?: string[]
  category?: string
  custom?: Record<string, unknown>
  allowed_templates?: string[]
}

export interface FileListResponse {
  items: FileEntity[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface FileDownloadResponse {
  file_id: string
  filename: string
  content_type: string
  size_bytes: number
  download_url: string
  expires_in: number
}

// =============================================================================
// BULK OPERATION TYPES
// =============================================================================

export interface FileBulkDeleteRequest {
  file_ids: string[]
}

export interface FileBulkResult {
  index: number
  status: 'success' | 'error'
  file_id: string | null
  error: string | null
}

export interface FileBulkDeleteResponse {
  total: number
  deleted: number
  failed: number
  results: FileBulkResult[]
}

// =============================================================================
// INTEGRITY TYPES
// =============================================================================

export interface FileIntegrityIssue {
  type: 'orphan_file' | 'missing_storage' | 'broken_reference'
  severity: 'warning' | 'error'
  file_id: string | null
  document_id: string | null
  field_path: string | null
  message: string
}

export interface FileIntegritySummary {
  total_files: number
  orphan_files: number
  missing_storage: number
  broken_references: number
}

export interface FileIntegrityResponse {
  status: 'healthy' | 'warning' | 'error'
  checked_at: string
  summary: Record<string, number>
  issues: FileIntegrityIssue[]
}

// =============================================================================
// QUERY TYPES
// =============================================================================

export interface FileQueryParams {
  status?: FileStatus
  content_type?: string
  category?: string
  tags?: string
  uploaded_by?: string
  page?: number
  page_size?: number
}
