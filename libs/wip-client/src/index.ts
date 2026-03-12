// Client factory
export { createWipClient, type WipClient, type WipClientConfig } from './client.js'

// Auth providers
export { type AuthProvider, ApiKeyAuthProvider, OidcAuthProvider } from './auth/index.js'

// Errors
export {
  WipError,
  WipValidationError,
  WipNotFoundError,
  WipConflictError,
  WipAuthError,
  WipServerError,
  WipNetworkError,
  WipBulkItemError,
} from './errors.js'

// Transport (for advanced usage)
export { FetchTransport, type FetchTransportConfig, type RetryConfig } from './http.js'

// Service classes (for advanced usage)
export { DefStoreService } from './services/def-store.js'
export { TemplateStoreService } from './services/template-store.js'
export { DocumentStoreService } from './services/document-store.js'
export { FileStoreService } from './services/file-store.js'
export { RegistryService } from './services/registry.js'
export { ReportingSyncService } from './services/reporting-sync.js'

// Types
export * from './types/index.js'

// Utilities
export * from './utils/index.js'
