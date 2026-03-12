import type { AuthProvider } from './index.js'

export class ApiKeyAuthProvider implements AuthProvider {
  constructor(private apiKey: string) {}

  getHeaders(): Record<string, string> {
    return { 'X-API-Key': this.apiKey }
  }

  setApiKey(key: string) {
    this.apiKey = key
  }
}
