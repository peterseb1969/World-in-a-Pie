import type { FetchTransport } from '../http.js'
import type { BulkResponse, BulkResultItem } from '../types/common.js'
import { WipBulkItemError } from '../errors.js'

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

  protected post<T>(path: string, body?: unknown, params?: Record<string, unknown> | object): Promise<T> {
    return this.transport.request<T>('POST', `${this.basePath}${path}`, {
      body,
      params: params as Record<string, unknown>,
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

  protected getBlob(path: string, params?: Record<string, unknown> | object): Promise<Blob> {
    return this.transport.request<Blob>('GET', `${this.basePath}${path}`, {
      params: params as Record<string, unknown>,
      responseType: 'blob',
    })
  }

  protected postFormData<T>(path: string, formData: FormData, params?: Record<string, unknown> | object): Promise<T> {
    return this.transport.request<T>('POST', `${this.basePath}${path}`, {
      body: formData,
      params: params as Record<string, unknown>,
    })
  }

  protected bulkWrite(
    path: string,
    items: unknown[],
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST',
  ): Promise<BulkResponse> {
    return this.transport.request<BulkResponse>(method, `${this.basePath}${path}`, {
      body: items,
    })
  }

  protected async bulkWriteOne(
    path: string,
    item: unknown,
    method: 'POST' | 'PUT' | 'PATCH' | 'DELETE' = 'POST',
  ): Promise<BulkResultItem> {
    const resp = await this.bulkWrite(path, [item], method)
    const result = resp.results[0]
    if (result.status === 'error') {
      throw new WipBulkItemError(
        result.error || 'Operation failed',
        result.index,
        result.status,
        result.error_code,
      )
    }
    return result
  }
}
