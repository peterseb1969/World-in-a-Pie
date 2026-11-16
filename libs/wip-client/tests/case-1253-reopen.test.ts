import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { FetchTransport } from '../src/http'
import { createWipClient } from '../src/client'
import { WipServerError, WipNetworkError } from '../src/errors'

// A 200 whose body is not JSON is not an answer, and a request the server is
// allowed to run long is not cut off by the client's default timeout first.

describe('a 200 body that is not JSON', () => {
  let transport: FetchTransport
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    transport = new FetchTransport({ baseUrl: 'http://localhost', retry: { maxRetries: 0 } })
  })
  afterEach(() => vi.restoreAllMocks())

  it.each([
    ['truncated JSON', '{"items": [1, 2'],
    ['an HTML page', '<html>proxy error</html>'],
  ])('is an error, not undefined: %s', async (_kind, body) => {
    vi.mocked(fetch).mockResolvedValue(new Response(body, { status: 200 }))
    await expect(transport.request('GET', '/api/x')).rejects.toBeInstanceOf(WipServerError)
  })

  it('an empty 200 body still reads as no content', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('', { status: 200 }))
    await expect(transport.request('GET', '/api/x')).resolves.toBeUndefined()
  })
})

describe('the client timeout follows the server timeout a call asks for', () => {
  let release: ((r: Response) => void) | undefined

  beforeEach(() => {
    vi.useFakeTimers()
    release = undefined
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise<Response>((resolve, reject) => {
      release = resolve
      init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })))
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  const client = () => createWipClient({
    baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'k' }, retry: { maxRetries: 0 },
  })

  async function outlivesDefault(call: Promise<unknown>, body: string, waitMs: number) {
    let settled: unknown = 'pending'
    call.then((v) => { settled = v }, (e) => { settled = e })
    await vi.advanceTimersByTimeAsync(waitMs)
    expect(settled).toBe('pending')
    release?.(new Response(body, { status: 200 }))
    await vi.runAllTimersAsync()
    expect(settled).not.toBeInstanceOf(WipNetworkError)
  }

  it('exportTableCsv waits for the server default (120 s), not 30 s', async () => {
    await outlivesDefault(client().reporting.exportTableCsv('doc_t', 'ns'), 'a,b\n', 100_000)
  })

  it('exportTableCsv waits for an explicit timeoutSeconds', async () => {
    await outlivesDefault(
      client().reporting.exportTableCsv('doc_t', 'ns', { timeoutSeconds: 500 }), 'a,b\n', 450_000,
    )
  })

  it('exportQueryCsv waits for an explicit timeoutSeconds', async () => {
    await outlivesDefault(
      client().reporting.exportQueryCsv('SELECT 1', { timeoutSeconds: 300 }), 'a\n', 250_000,
    )
  })

  it('runQuery waits for an explicit timeout_seconds', async () => {
    await outlivesDefault(
      client().reporting.runQuery('SELECT 1', [], { timeout_seconds: 200 }),
      '{"columns": [], "rows": [], "row_count": 0}', 150_000,
    )
  })
})
