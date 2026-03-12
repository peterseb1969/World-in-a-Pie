/** Auth provider interface — implementations supply headers for each request. */
export interface AuthProvider {
  getHeaders(): Record<string, string> | Promise<Record<string, string>>
}

export { ApiKeyAuthProvider } from './api-key.js'
export { OidcAuthProvider } from './oidc.js'
