import type { AuthProvider } from './index.js'

export class ApiKeyAuthProvider implements AuthProvider {
  // The key is static — safe (and cheap) for the transport to cache. To rotate
  // it, give the client a new provider: client.setAuth(new ApiKeyAuthProvider(key)),
  // which also clears the cache. (A setter on this provider could not reach
  // the transport's cache, so a live client kept sending the old key.)
  readonly cacheable = true

  constructor(private readonly apiKey: string) {}

  getHeaders(): Record<string, string> {
    return { 'X-API-Key': this.apiKey }
  }
}
