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

  // The view-rows route runs its statement for timeout_seconds (1-300,
  // default 30): the client gives the call that long plus the grace.
  it('readViewRows waits for an explicit timeout_seconds', async () => {
    await outlivesDefault(
      client().reporting.readViewRows('clinic-a', 'v_recent', { timeout_seconds: 120, limit: 1000 }),
      '{"columns": [], "rows": [], "row_count": 0}', 100_000,
    )
  })

  it('readViewRows without a timeout waits through the server default and its grace', async () => {
    await outlivesDefault(
      client().reporting.readViewRows('clinic-a', 'v_recent'),
      '{"columns": [], "rows": [], "row_count": 0}', 35_000,
    )
  })
})

// Every reporting-sync call whose route lets its work run past the client's
// 30 s default, each held open just short of the longest the server may take
// for that request. A new long-running call belongs in this table.
describe('every reporting call the server may run long outlives the client default', () => {
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

  const reporting = () => createWipClient({
    baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'k' }, retry: { maxRetries: 0 },
  }).reporting

  const JSON_ROWS = '{"columns": [], "rows": [], "row_count": 0}'
  const INTEGRITY = '{"status": "healthy", "services_checked": [], "summary": {}, "issues": []}'

  it.each<[string, () => Promise<unknown>, string, number]>([
    ['runQuery at the 300 s maximum', () => reporting().runQuery('SELECT 1', [], { timeout_seconds: 300 }), JSON_ROWS, 300_000],
    ['readViewRows at the 300 s maximum', () => reporting().readViewRows('ns', 'v', { timeout_seconds: 300 }), JSON_ROWS, 300_000],
    ['exportTableCsv at the 600 s maximum', () => reporting().exportTableCsv('doc_t', 'ns', { timeoutSeconds: 600 }), 'a\n', 600_000],
    ['exportQueryCsv at the 600 s maximum', () => reporting().exportQueryCsv('SELECT 1', { timeoutSeconds: 600 }), 'a\n', 600_000],
    // The route's default is a full document scan, which document-store
    // is given 1800 s for.
    ['getIntegrityCheck with no limit (full scan, 1800 s)', () => reporting().getIntegrityCheck(), INTEGRITY, 1_800_000],
    ['getIntegrityCheck with document_limit 0 (1800 s)', () => reporting().getIntegrityCheck({ document_limit: 0 }), INTEGRITY, 1_800_000],
    ['getIntegrityCheck with document_limit 1000 (120 s floor)', () => reporting().getIntegrityCheck({ document_limit: 1000 }), INTEGRITY, 120_000],
    ['getIntegrityCheck with document_limit 50000 (600 s)', () => reporting().getIntegrityCheck({ document_limit: 50_000 }), INTEGRITY, 600_000],
  ])('%s', async (_name, call, body, serverMs) => {
    let settled: unknown = 'pending'
    call().then((v) => { settled = v }, (e) => { settled = e })
    await vi.advanceTimersByTimeAsync(serverMs)
    expect(settled).toBe('pending')
    release?.(new Response(body, { status: 200 }))
    await vi.runAllTimersAsync()
    expect(settled).not.toBe('pending')
    expect(settled).not.toBeInstanceOf(WipNetworkError)
  })

  it('a call with no server budget still gives up at the client default', async () => {
    let settled: unknown = 'pending'
    reporting().getSyncStatus().then((v) => { settled = v }, (e) => { settled = e })
    await vi.advanceTimersByTimeAsync(31_000)
    expect(settled).toBeInstanceOf(WipNetworkError)
  })
})
