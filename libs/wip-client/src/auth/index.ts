/** Auth provider interface — implementations supply headers for each request. */
export interface AuthProvider {
  getHeaders(): Record<string, string> | Promise<Record<string, string>>
  /**
   * Whether the transport may cache this provider's headers across requests.
   * Static credentials (API keys) set this true; rotating credentials that
   * the transport must re-fetch every request (OIDC bearer tokens, which the
   * consumer's callback refreshes) leave it false/undefined so an expiring
   * token never pins a stale header (CASE-569).
   */
  cacheable?: boolean
}

export { ApiKeyAuthProvider } from './api-key.js'
export { OidcAuthProvider } from './oidc.js'
