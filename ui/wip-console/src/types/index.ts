// Re-export all types
export * from './terminology'
export * from './template'
export * from './document'
export * from './file'
export * from './registry'
export * from './ontology'

// =============================================================================
// SHARED TYPES
// =============================================================================

export interface ApiError {
  detail: string | Record<string, unknown>
}

// =============================================================================
// BULK OPERATION TYPES (shared across all services)
// =============================================================================

export interface BulkResultItem {
  index: number
  status: string
  id?: string
  error?: string
  // Def-Store / Template-Store
  value?: string
  // Template-Store
  version?: number
  is_new_version?: boolean
  // Document-Store
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
