import { describe, it, expect, vi, afterEach } from 'vitest'
import { FetchTransport } from '../src/http'
import { createWipClient } from '../src/client'
import { WipError, WipNotFoundError, WipServerError, WipValidationError } from '../src/errors'
import { bulkImport } from '../src/utils/bulk-import'
import { resolveReference } from '../src/utils/resolve-reference'
import * as entry from '../src/index'
import type { BulkResponse } from '../src/types/common'

// CASE-1255: the wip-client findings of the area-18 inspect sweep that need
// code, not only a corrected comment.

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function client() {
  return createWipClient({ baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'k' } })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('retries', () => {
  it('a GET answered 500 is not retried: only 502/503/504 are', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json({ detail: 'boom' }, 500))
    vi.stubGlobal('fetch', fetchMock)
    const t = new FetchTransport({ baseUrl: 'http://localhost', retry: { maxRetries: 2, baseDelayMs: 1 } })
    await expect(t.request('GET', '/x')).rejects.toBeInstanceOf(WipServerError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it.each([502, 503, 504])('a GET answered %i is still retried', async (status) => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({}, status)).mockResolvedValueOnce(json({ ok: 1 }))
    vi.stubGlobal('fetch', fetchMock)
    const t = new FetchTransport({ baseUrl: 'http://localhost', retry: { maxRetries: 1, baseDelayMs: 1 } })
    await expect(t.request('GET', '/x')).resolves.toEqual({ ok: 1 })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('validation errors carry the status the service answered', () => {
  it.each([400, 422])('%i', async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'bad' }, status)))
    const t = new FetchTransport({ baseUrl: 'http://localhost', retry: { maxRetries: 0 } })
    const err = await t.request('GET', '/x').catch((e) => e)
    expect(err).toBeInstanceOf(WipValidationError)
    expect(err.statusCode).toBe(status)
  })
})

describe('a single-item write without a result', () => {
  it.each([
    ['a 204', () => new Response(null, { status: 204 })],
    ['an empty 200', () => new Response('', { status: 200 })],
    ['no results', () => json({ total: 0 })],
    ['empty results', () => json({ results: [], total: 0, succeeded: 0, failed: 0 })],
  ])('%s throws a WipError naming the call, not a TypeError', async (_name, answer) => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => answer()))
    const err = await client().documents.createDocument({ template_id: 'T', namespace: 'ns', data: {} }).catch((e) => e)
    expect(err).toBeInstanceOf(WipError)
    expect(err).not.toBeInstanceOf(TypeError)
    expect(err.message).toContain('answered without a result')
  })
})

describe('bulkImport keeps its progress when a batch request rejects', () => {
  const ok = (n: number): BulkResponse => ({ results: [], total: n, succeeded: n, failed: 0 })

  it.each([1, 3])('continueOnError (concurrency %i): the rejected batch counts as failed, the rest go on', async (concurrency) => {
    const outage = new WipServerError('down', 503)
    const writeFn = vi.fn(async (batch: number[]) => {
      if (batch[0] === 2) throw outage
      return ok(batch.length)
    })
    const progress = await bulkImport([0, 1, 2, 3, 4, 5], writeFn, { batchSize: 2, concurrency })
    expect(progress).toMatchObject({ processed: 6, total: 6, succeeded: 4, failed: 2 })
    expect(progress.rejected).toEqual([{ offset: 2, size: 2, error: outage }])
    expect(writeFn).toHaveBeenCalledTimes(3)
  })

  it('continueOnError false: stops after the rejected batch with what was done before it', async () => {
    const writeFn = vi.fn(async (batch: number[]) => {
      if (batch[0] === 2) throw new Error('refused')
      return ok(batch.length)
    })
    const progress = await bulkImport([0, 1, 2, 3, 4, 5], writeFn, { batchSize: 2, continueOnError: false })
    expect(progress).toMatchObject({ processed: 4, succeeded: 2, failed: 2 })
    expect(progress.rejected).toHaveLength(1)
    expect(writeFn).toHaveBeenCalledTimes(2)
  })
})

describe('streamBackupJobEvents', () => {
  function sse(text: string): Response {
    return new Response(new Blob([text]).stream(), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }

  it('throws on the error event the server sends when the job disappears', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sse(
      ': connected\n\n'
      + 'event: progress\ndata: {"job_id":"j","status":"running","phase":"docs","percent":10,"message":null}\n\n'
      + 'event: error\ndata: {"error": "Backup job j not found"}\n\n',
    )))
    const seen: unknown[] = []
    const err = await (async () => {
      for await (const evt of client().documents.streamBackupJobEvents('j')) seen.push(evt)
    })().catch((e) => e)
    expect(seen).toHaveLength(1)
    expect(err).toBeInstanceOf(WipNotFoundError)
    expect(err.message).toBe('Backup job j not found')
  })

  it('a stream that reaches the end without an error event ends normally', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sse(
      'event: progress\ndata: {"job_id":"j","status":"complete","phase":null,"percent":100,"message":null}\n\n',
    )))
    const seen: unknown[] = []
    for await (const evt of client().documents.streamBackupJobEvents('j')) seen.push(evt)
    expect(seen).toHaveLength(1)
  })
})

describe('requests carry what the service declares', () => {
  it('resolveReference passes the namespace a value-form template needs', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ items: [], total: 0, page: 1, page_size: 100, pages: 0 }))
    vi.stubGlobal('fetch', fetchMock)
    await resolveReference(client(), 'PERSON', 'x', 10, 'ns-a')
    expect(new URL(String(fetchMock.mock.calls[0][0])).searchParams.get('namespace')).toBe('ns-a')
  })

  it('createDocuments sends continue_on_error only when given', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => json({ results: [], total: 0, succeeded: 0, failed: 0 }))
    vi.stubGlobal('fetch', fetchMock)
    const c = client()
    await c.documents.createDocuments([], { continueOnError: false })
    await c.documents.createDocuments([])
    const urls = fetchMock.mock.calls.map((call) => new URL(String(call[0])))
    expect(urls[0].searchParams.get('continue_on_error')).toBe('false')
    expect(urls[1].searchParams.has('continue_on_error')).toBe(false)
  })

  it('BaseService is exported from the package entry, as the README says', () => {
    expect(typeof entry.BaseService).toBe('function')
  })
})
