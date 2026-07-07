import type { AuthProvider } from './index.js'

export class ApiKeyAuthProvider implements AuthProvider {
  // The key is static — safe (and cheap) for the transport to cache. Rotation
  // goes through setApiKey + the transport's setAuth, which clears the cache.
  readonly cacheable = true

  constructor(private apiKey: string) {}

  getHeaders(): Record<string, string> {
    return { 'X-API-Key': this.apiKey }
  }

  setApiKey(key: string) {
    this.apiKey = key
  }
}
