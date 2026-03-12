import type { PaginatedResponse } from './common.js'

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

export type FileListResponse = PaginatedResponse<FileEntity>

export interface FileDownloadResponse {
  file_id: string
  filename: string
  content_type: string
  size_bytes: number
  download_url: string
  expires_in: number
}

export interface FileIntegrityIssue {
  type: 'orphan_file' | 'missing_storage' | 'broken_reference'
  severity: 'warning' | 'error'
  file_id: string | null
  document_id: string | null
  field_path: string | null
  message: string
}

export interface FileIntegrityResponse {
  status: 'healthy' | 'warning' | 'error'
  checked_at: string
  summary: Record<string, number>
  issues: FileIntegrityIssue[]
}

export interface FileQueryParams {
  namespace?: string
  status?: FileStatus
  content_type?: string
  category?: string
  tags?: string
  uploaded_by?: string
  page?: number
  page_size?: number
}
