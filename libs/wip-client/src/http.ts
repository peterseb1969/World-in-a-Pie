import {
  WipError,
  WipValidationError,
  WipNotFoundError,
  WipConflictError,
  WipAuthError,
  WipServerError,
  WipNetworkError,
} from './errors.js'
import type { AuthProvider } from './auth/index.js'

export interface FetchTransportConfig {
  baseUrl: string
  auth?: AuthProvider
  timeout?: number
  retry?: RetryConfig
  onAuthError?: () => void
}

export interface RetryConfig {
  maxRetries: number
  baseDelayMs?: number
  maxDelayMs?: number
}

const DEFAULT_TIMEOUT = 30_000
const DEFAULT_RETRY: RetryConfig = { maxRetries: 2, baseDelayMs: 500, maxDelayMs: 5_000 }

export class FetchTransport {
  private baseUrl: string
  private auth?: AuthProvider
  private timeout: number
  private retry: RetryConfig
  private onAuthError?: () => void
  // Cache auth headers to avoid async overhead on synchronous providers
  private cachedAuthHeaders: Record<string, string> | null = null

  constructor(config: FetchTransportConfig) {
    // Resolve empty baseUrl: use window.location.origin in browsers, error in Node
    let baseUrl = config.baseUrl
    if (!baseUrl) {
      if (typeof window !== 'undefined' && window.location?.origin) {
        baseUrl = window.location.origin
      } else {
        throw new Error(
          'WipClient: baseUrl is required in non-browser environments. ' +
          'Pass an explicit baseUrl (e.g. "http://localhost:8001").'
        )
      }
    }
    // Strip trailing slash
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    this.auth = config.auth
    this.timeout = config.timeout ?? DEFAULT_TIMEOUT
    this.retry = config.retry ?? DEFAULT_RETRY
    this.onAuthError = config.onAuthError
  }

  setAuth(auth: AuthProvider | undefined) {
    this.auth = auth
    this.cachedAuthHeaders = null
  }

  async request<T>(
    method: string,
    path: string,
    options?: {
      body?: unknown
      params?: Record<string, unknown>
      headers?: Record<string, string>
      responseType?: 'json' | 'blob' | 'text'
      timeout?: number
    },
  ): Promise<T> {
    const url = this.buildUrl(path, options?.params)
    const headers: Record<string, string> = {
      ...options?.headers,
    }

    // Add auth headers (cache for synchronous providers to avoid async overhead)
    if (this.auth) {
      if (!this.cachedAuthHeaders) {
        this.cachedAuthHeaders = await this.auth.getHeaders()
      }
      Object.assign(headers, this.cachedAuthHeaders)
    }

    // Set content-type for JSON bodies
    if (options?.body !== undefined && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json'
    }

    const fetchOptions: RequestInit = {
      method,
      headers,
      // Enable HTTP keep-alive for connection reuse across batches
      keepalive: true,
    }

    if (options?.body !== undefined) {
      fetchOptions.body =
        options.body instanceof FormData
          ? options.body
          : JSON.stringify(options.body)
    }

    const isRetryable = method === 'GET'
    const maxAttempts = isRetryable ? this.retry.maxRetries + 1 : 1
    let lastError: Error | undefined

    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (attempt > 0) {
        const delay = Math.min(
          (this.retry.baseDelayMs ?? 500) * 2 ** (attempt - 1),
          this.retry.maxDelayMs ?? 5_000,
        )
        await new Promise((r) => setTimeout(r, delay))
      }

      const controller = new AbortController()
      const timeoutMs = options?.timeout ?? this.timeout
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
      fetchOptions.signal = controller.signal

      try {
        const response = await fetch(url, fetchOptions)
        clearTimeout(timeoutId)

        if (!response.ok) {
          const error = await this.mapResponseError(response)
          if (error instanceof WipAuthError) {
            // Invalidate cached auth headers on auth error
            this.cachedAuthHeaders = null
            if (this.onAuthError) this.onAuthError()
          }
          // Retry on 502/503/504 for GET
          if (isRetryable && attempt < maxAttempts - 1 && response.status >= 502) {
            lastError = error
            continue
          }
          throw error
        }

        const responseType = options?.responseType ?? 'json'
        if (responseType === 'blob') {
          return (await response.blob()) as T
        }
        if (responseType === 'text') {
          return (await response.text()) as T
        }

        // Handle empty responses (204, etc.)
        if (response.status === 204) {
          return undefined as T
        }
        // Use response.json() directly — faster than text() + JSON.parse()
        // Fall back gracefully if body is empty (some proxies return 200 with no body)
        try {
          return (await response.json()) as T
        } catch {
          return undefined as T
        }
      } catch (err) {
        clearTimeout(timeoutId)
        if (err instanceof WipError) {
          if (isRetryable && attempt < maxAttempts - 1 && err instanceof WipServerError) {
            lastError = err
            continue
          }
          throw err
        }
        if (err instanceof DOMException && err.name === 'AbortError') {
          lastError = new WipNetworkError(`Request timed out after ${timeoutMs}ms`)
          if (attempt < maxAttempts - 1) continue
          throw lastError
        }
        lastError = new WipNetworkError(
          err instanceof Error ? err.message : 'Network error',
          err instanceof Error ? err : undefined,
        )
        if (attempt < maxAttempts - 1) continue
        throw lastError
      }
    }

    throw lastError ?? new WipNetworkError('Request failed after all retries')
  }

  private buildUrl(path: string, params?: Record<string, unknown>): string {
    const fullUrl = `${this.baseUrl}${path.startsWith('/') ? path : '/' + path}`

    if (!params) return fullUrl

    const parsed = new URL(fullUrl)
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null) continue
      if (Array.isArray(value)) {
        for (const v of value) {
          parsed.searchParams.append(key, String(v))
        }
      } else if (typeof value === 'boolean') {
        parsed.searchParams.set(key, value ? 'true' : 'false')
      } else {
        parsed.searchParams.set(key, String(value))
      }
    }

    return parsed.toString()
  }

  private async mapResponseError(response: Response): Promise<WipError> {
    let detail: unknown
    try {
      const text = await response.text()
      detail = text ? JSON.parse(text) : undefined
    } catch {
      detail = undefined
    }

    const message = this.extractMessage(detail, response.statusText)

    switch (response.status) {
      case 400:
        return new WipValidationError(message, detail)
      case 401:
      case 403:
        return new WipAuthError(message, response.status, detail)
      case 404:
        return new WipNotFoundError(message, detail)
      case 409:
        return new WipConflictError(message, detail)
      case 422:
        return new WipValidationError(message, detail)
      default:
        if (response.status >= 500) {
          return new WipServerError(message, response.status, detail)
        }
        return new WipError(message, response.status, detail)
    }
  }

  private extractMessage(detail: unknown, fallback: string): string {
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      const d = detail as Record<string, unknown>
      if (typeof d.detail === 'string') return d.detail
      if (typeof d.detail === 'object' && d.detail !== null) {
        const inner = d.detail as Record<string, unknown>
        return String(inner.message || inner.error || JSON.stringify(d.detail))
      }
      if (typeof d.message === 'string') return d.message
      if (typeof d.error === 'string') return d.error
    }
    return fallback
  }
}
