import type { FetchTransport } from '../http.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import { WipBulkItemError, WipError } from '../errors.js'

export abstract class BaseService {
  constructor(
    protected readonly transport: FetchTransport,
    protected readonly basePath: string,
  ) {}

  protected get<T>(path: string, params?: Record<string, unknown> | object): Promise<T> {
    return this.transport.request<T>('GET', `${this.basePath}${path}`, {
      params: params as Record<string, unknown>,
    })
  }

  /** `minTimeout` (ms) raises the client timeout for a call the server may
   * run longer than it; it never lowers a larger configured timeout. */
  protected post<T>(
    path: string, body?: unknown, params?: Record<string, unknown> | object, minTimeout?: number,
  ): Promise<T> {
    return this.transport.request<T>('POST', `${this.basePath}${path}`, {
      body,
      params: params as Record<string, unknown>,
      minTimeout,
    })
  }

  protected put<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T> {
    return this.transport.request<T>('PUT', `${this.basePath}${path}`, {
      body,
      params: params as Record<string, unknown>,
    })
  }

  protected patch<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T> {
    return this.transport.request<T>('PATCH', `${this.basePath}${path}`, {
      body,
      params: params as Record<string, unknown>,
    })
  }

  protected del<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T> {
    return this.transport.request<T>('DELETE', `${this.basePath}${path}`, {
      body,
      params: params as Record<string, unknown>,
    })
  }

  protected getBlob(
    path: string, params?: Record<string, unknown> | object, minTimeout?: number,
  ): Promise<Blob> {
    return this.transport.request<Blob>('GET', `${this.basePath}${path}`, {
      params: params as Record<string, unknown>,
      responseType: 'blob',
      minTimeout,
    })
  }

  protected postBlob(
    path: string, body?: unknown, params?: Record<string, unknown> | object, minTimeout?: number,
  ): Promise<Blob> {
    return this.transport.request<Blob>('POST', `${this.basePath}${path}`, {
      body,
      params: params as Record<string, unknown>,
      responseType: 'blob',
      minTimeout,
    })
  }

  protected postFormData<T>(path: string, formData: FormData, params?: Record<string, unknown> | object): Promise<T> {
    return this.transport.request<T>('POST', `${this.basePath}${path}`, {
      body: formData,
      params: params as Record<string, unknown>,
    })
  }

  protected stream(
    method: string,
    path: string,
    options?: {
      params?: Record<string, unknown>
      signal?: AbortSignal
      headers?: Record<string, string>
    },
  ): Promise<Response> {
    return this.transport.stream(method, `${this.basePath}${path}`, options)
  }

  protected bulkWrite(
    path: string,
    items: unknown[],
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST',
    params?: Record<string, unknown>,
  ): Promise<BulkResponse> {
    return this.transport.request<BulkResponse>(method, `${this.basePath}${path}`, {
      body: items,
      params,
    })
  }

  protected async bulkWriteOne(
    path: string,
    item: unknown,
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST',
    params?: Record<string, unknown>,
  ): Promise<BulkResultItem> {
    const resp = await this.bulkWrite(path, [item], method, params)
    // A 204, an empty body (some proxies answer 200 with none) or a body
    // without results carries no outcome for the item: say so, rather than
    // failing on a property of undefined.
    const result = Array.isArray(resp?.results) ? resp.results[0] : undefined
    if (result === undefined) {
      throw new WipError(
        `${method} ${this.basePath}${path} answered without a result for the item`,
        undefined,
        resp,
      )
    }
    if (result.status === 'error') {
      throw new WipBulkItemError(
        result.error || 'Operation failed',
        result.index,
        result.status,
        result.error_code,
        result.details,
      )
    }
    return result
  }
}
