export interface BulkResultItem {
  index: number
  status: string
  id?: string
  error?: string
  /** Machine-readable error code, set when status === "error". See per-endpoint docs for the code matrix. */
  error_code?: string
  /** Structured details for non-error statuses (e.g. compatibility diff for on_conflict=validate). */
  details?: Record<string, unknown>
  value?: string
  version?: number
  is_new_version?: boolean
  document_id?: string
  identity_hash?: string
  is_new?: boolean
  warnings?: string[]
}

export interface BulkResponse {
  results: BulkResultItem[]
  total: number
  succeeded: number
  failed: number
  skipped?: number
  timing?: Record<string, number>
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

export interface ApiError {
  detail: string | Record<string, unknown>
}
