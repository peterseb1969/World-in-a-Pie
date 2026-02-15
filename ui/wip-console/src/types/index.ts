// Re-export all types
export * from './terminology'
export * from './template'
export * from './document'
export * from './file'

// =============================================================================
// SHARED TYPES
// =============================================================================

export interface ApiError {
  detail: string | Record<string, unknown>
}
