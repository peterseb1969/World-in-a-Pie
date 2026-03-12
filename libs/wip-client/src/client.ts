import { FetchTransport, type FetchTransportConfig, type RetryConfig } from './http.js'
import type { AuthProvider } from './auth/index.js'
import { ApiKeyAuthProvider } from './auth/api-key.js'
import { OidcAuthProvider } from './auth/oidc.js'
import { DefStoreService } from './services/def-store.js'
import { TemplateStoreService } from './services/template-store.js'
import { DocumentStoreService } from './services/document-store.js'
import { FileStoreService } from './services/file-store.js'
import { RegistryService } from './services/registry.js'
import { ReportingSyncService } from './services/reporting-sync.js'

export interface WipClientConfig {
  baseUrl: string
  auth?: AuthProvider | { type: 'api-key'; key: string } | { type: 'oidc'; getToken: () => string | Promise<string> }
  timeout?: number
  retry?: RetryConfig
  onAuthError?: () => void
}

export interface WipClient {
  defStore: DefStoreService
  templates: TemplateStoreService
  documents: DocumentStoreService
  files: FileStoreService
  registry: RegistryService
  reporting: ReportingSyncService
  setAuth(auth: AuthProvider): void
}

function resolveAuth(auth: WipClientConfig['auth']): AuthProvider | undefined {
  if (!auth) return undefined
  if ('getHeaders' in auth) return auth as AuthProvider
  if (auth.type === 'api-key') return new ApiKeyAuthProvider(auth.key)
  if (auth.type === 'oidc') return new OidcAuthProvider(auth.getToken)
  return undefined
}

export function createWipClient(config: WipClientConfig): WipClient {
  const transport = new FetchTransport({
    baseUrl: config.baseUrl,
    auth: resolveAuth(config.auth),
    timeout: config.timeout,
    retry: config.retry,
    onAuthError: config.onAuthError,
  })

  return {
    defStore: new DefStoreService(transport),
    templates: new TemplateStoreService(transport),
    documents: new DocumentStoreService(transport),
    files: new FileStoreService(transport),
    registry: new RegistryService(transport),
    reporting: new ReportingSyncService(transport),
    setAuth(auth: AuthProvider) {
      transport.setAuth(auth)
    },
  }
}
