import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { FetchTransport } from '../src/http'
import {
  WipValidationError,
  WipNotFoundError,
  WipAuthError,
  WipServerError,
  WipNetworkError,
} from '../src/errors'

describe('FetchTransport', () => {
  let transport: FetchTransport
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    transport = new FetchTransport({
      baseUrl: 'http://localhost:8001',
      retry: { maxRetries: 0 },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('makes GET request and parses JSON', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    const result = await transport.request('GET', '/api/test')
    expect(result).toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledOnce()

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8001/api/test')
    expect(options.method).toBe('GET')
  })

  it('sends JSON body on POST', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: '1' }), { status: 200 }))

    await transport.request('POST', '/api/items', { body: { name: 'test' } })

    const [, options] = fetchMock.mock.calls[0]
    expect(options.method).toBe('POST')
    expect(options.body).toBe('{"name":"test"}')
    expect(options.headers['Content-Type']).toBe('application/json')
  })

  it('appends query params', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))

    await transport.request('GET', '/api/items', {
      params: { page: 1, status: 'active', tags: undefined },
    })

    const [url] = fetchMock.mock.calls[0]
    expect(url).toContain('page=1')
    expect(url).toContain('status=active')
    expect(url).not.toContain('tags')
  })

  it('handles boolean params', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))

    await transport.request('GET', '/api/items', {
      params: { include_archived: true },
    })

    const [url] = fetchMock.mock.calls[0]
    expect(url).toContain('include_archived=true')
  })

  it('maps 404 to WipNotFoundError', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Not found' }), { status: 404, statusText: 'Not Found' }),
    )

    await expect(transport.request('GET', '/api/missing')).rejects.toThrow(WipNotFoundError)
  })

  it('maps 401 to WipAuthError', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401, statusText: 'Unauthorized' }),
    )

    await expect(transport.request('GET', '/api/secure')).rejects.toThrow(WipAuthError)
  })

  it('maps 422 to WipValidationError', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Invalid' }), { status: 422, statusText: 'Unprocessable Entity' }),
    )

    await expect(transport.request('POST', '/api/items', { body: {} })).rejects.toThrow(WipValidationError)
  })

  it('maps 500 to WipServerError', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Server error' }), { status: 500, statusText: 'Internal Server Error' }),
    )

    await expect(transport.request('GET', '/api/broken')).rejects.toThrow(WipServerError)
  })

  it('injects auth headers', async () => {
    const authedTransport = new FetchTransport({
      baseUrl: 'http://localhost:8001',
      auth: { getHeaders: () => ({ 'X-API-Key': 'test-key' }) },
      retry: { maxRetries: 0 },
    })

    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }))
    await authedTransport.request('GET', '/api/test')

    const [, options] = fetchMock.mock.calls[0]
    expect(options.headers['X-API-Key']).toBe('test-key')
  })

  it('calls onAuthError on 401', async () => {
    const onAuthError = vi.fn()
    const authedTransport = new FetchTransport({
      baseUrl: 'http://localhost:8001',
      onAuthError,
      retry: { maxRetries: 0 },
    })

    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Token expired' }), { status: 401, statusText: 'Unauthorized' }),
    )

    await expect(authedTransport.request('GET', '/api/test')).rejects.toThrow(WipAuthError)
    expect(onAuthError).toHaveBeenCalledOnce()
  })

  it('handles empty response body (204)', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

    const result = await transport.request('DELETE', '/api/items/1')
    expect(result).toBeUndefined()
  })

  it('retries GET on 502', async () => {
    const retryTransport = new FetchTransport({
      baseUrl: 'http://localhost:8001',
      retry: { maxRetries: 1, baseDelayMs: 10 },
    })

    fetchMock
      .mockResolvedValueOnce(new Response('Bad Gateway', { status: 502 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    const result = await retryTransport.request('GET', '/api/test')
    expect(result).toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not retry POST', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'error' }), { status: 502, statusText: 'Bad Gateway' }),
    )

    const retryTransport = new FetchTransport({
      baseUrl: 'http://localhost:8001',
      retry: { maxRetries: 2, baseDelayMs: 10 },
    })

    await expect(retryTransport.request('POST', '/api/test', { body: {} })).rejects.toThrow(WipServerError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('uses window.location.origin when baseUrl is empty string', () => {
    vi.stubGlobal('window', { location: { origin: 'https://wip.local:8443' } })
    const t = new FetchTransport({ baseUrl: '' })
    // Verify by making a request — the URL should use the origin
    fetchMock.mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }))
    t.request('GET', '/api/test')
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('https://wip.local:8443/api/test'),
      expect.any(Object),
    )
  })

  it('resolves relative baseUrl against window.location.origin', () => {
    vi.stubGlobal('window', { location: { origin: 'http://localhost:5173' } })
    const t = new FetchTransport({ baseUrl: '/wip', retry: { maxRetries: 0 } })
    fetchMock.mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }))
    t.request('GET', '/api/document-store/documents')
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:5173/wip/api/document-store/documents',
      expect.any(Object),
    )
  })

  it('resolves relative baseUrl with query params', async () => {
    vi.stubGlobal('window', { location: { origin: 'http://localhost:5173' } })
    const t = new FetchTransport({ baseUrl: '/wip', retry: { maxRetries: 0 } })
    fetchMock.mockResolvedValue(new Response(JSON.stringify({}), { status: 200 }))
    await t.request('GET', '/api/document-store/documents', {
      params: { namespace: 'test', status: 'active' },
    })
    const [url] = fetchMock.mock.calls[0]
    expect(url).toContain('http://localhost:5173/wip/api/document-store/documents')
    expect(url).toContain('namespace=test')
    expect(url).toContain('status=active')
  })

  it('throws clear error when baseUrl is empty in non-browser environment', () => {
    vi.stubGlobal('window', undefined)
    expect(() => new FetchTransport({ baseUrl: '' })).toThrow('baseUrl is required')
  })

  it('throws clear error when relative baseUrl used in non-browser environment', () => {
    vi.stubGlobal('window', undefined)
    expect(() => new FetchTransport({ baseUrl: '/wip' })).toThrow('relative baseUrl')
  })
})
