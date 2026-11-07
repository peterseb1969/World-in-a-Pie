/**
 * How the client surfaces the platform's answers for a Registry outage and
 * an ambiguous identifier (CASE-1087): a 503 with error_code
 * "registry_unavailable" is a WipServerError (retried on GET), and a 409
 * carrying its candidates is a WipConflictError whose detail keeps them.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { FetchTransport } from '../src/http'
import { WipConflictError, WipServerError } from '../src/errors'

const OUTAGE = {
  detail: 'Registry unavailable (resolve): HTTP 500; retry shortly',
  error_code: 'registry_unavailable',
}
const AMBIGUOUS = {
  detail: {
    error: 'ambiguous',
    error_code: 'ambiguous_reference',
    message: "document identifier 'X' is ambiguous",
    identifier: 'X',
    candidates: [{ entry_id: 'u1' }, { entry_id: 'u2' }],
    total: 2,
  },
}

describe('Registry outage and ambiguity answers', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('a 503 outage is a WipServerError carrying the error code', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(OUTAGE), { status: 503 }))
    const transport = new FetchTransport({ baseUrl: 'http://wip', retry: { maxRetries: 0 } })
    const err = await transport.request('GET', '/x').catch((e) => e)
    expect(err).toBeInstanceOf(WipServerError)
    expect(err.statusCode).toBe(503)
    expect(err.message).toBe(OUTAGE.detail)
    expect((err.detail as typeof OUTAGE).error_code).toBe('registry_unavailable')
  })

  it('a GET is retried through a 503 outage', async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify(OUTAGE), { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))
    const transport = new FetchTransport({
      baseUrl: 'http://wip', retry: { maxRetries: 1, baseDelayMs: 1, maxDelayMs: 1 },
    })
    expect(await transport.request('GET', '/x')).toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('a 409 ambiguity is a WipConflictError that keeps its candidates', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(AMBIGUOUS), { status: 409 }))
    const transport = new FetchTransport({ baseUrl: 'http://wip', retry: { maxRetries: 0 } })
    const err = await transport.request('GET', '/x').catch((e) => e)
    expect(err).toBeInstanceOf(WipConflictError)
    expect(err.message).toBe(AMBIGUOUS.detail.message)
    expect((err.detail as typeof AMBIGUOUS).detail.candidates).toHaveLength(2)
  })
})
