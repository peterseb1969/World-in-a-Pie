import type { AuthProvider } from './index.js'

/**
 * OIDC auth provider. Consumer provides a callback that returns the current token.
 * No OIDC library dependency — the consumer handles token acquisition/refresh.
 */
export class OidcAuthProvider implements AuthProvider {
  constructor(private getToken: () => string | Promise<string>) {}

  async getHeaders(): Promise<Record<string, string>> {
    const token = await this.getToken()
    return { Authorization: `Bearer ${token}` }
  }
}
