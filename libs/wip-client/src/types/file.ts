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
  namespace: string
  filename: string
  content_type: string
  size_bytes: number
  checksum: string
  storage_key: string
  metadata: FileMetadata
  status: FileStatus
  reference_count: number
  /**
   * The canonical template ids whose documents may reference this file, or null
   * when the file is open to every template. Entries are stored as ids: a
   * restriction given as a template value is resolved in the file's namespace
   * on write.
   */
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
  /**
   * Restrict the file to the templates whose documents may reference it: each
   * entry names a template (value, NS:VALUE, id or synonym) and is resolved to
   * its canonical id in the file's namespace. An entry that names no template
   * refuses the upload. Omit it, or send an empty list, for no restriction.
   * Re-uploading stored bytes returns the existing file: if that file carries a
   * different restriction the upload is refused (409) rather than silently
   * ignoring what was asked for.
   */
  allowed_templates?: string[]
}

export interface UpdateFileMetadataRequest {
  description?: string
  tags?: string[]
  category?: string
  custom?: Record<string, unknown>
  /**
   * Replaces the restriction (see FileUploadMetadata.allowed_templates); an
   * empty list clears it, and omitting the field leaves it unchanged.
   */
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
  /** `incomplete`: some stored objects could not be checked (the store refused,
   * or they were beyond the sweep's limit) and nothing worse was found. */
  status: 'healthy' | 'warning' | 'error' | 'incomplete'
  checked_at: string
  /** Issue counts by type, plus storage_checked, storage_check_failed and
   * storage_not_checked. */
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
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}
