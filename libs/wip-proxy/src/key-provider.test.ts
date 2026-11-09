import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createServer, type Server } from 'node:http'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import express from 'express'
import { wipProxy, keyFromFile, type WipProxyOptions, type WipProxyRouter } from './index.js'

/**
 * A key provider (`getApiKey`) is asked on every upstream request, so a key
 * that appears, changes or disappears takes effect without rebuilding the
 * router: a keyless proxy answers 503 and recovers as soon as a key exists.
 * The provider's value goes through the same trim-and-refuse as a static key;
 * a provider that returns nothing, a bad key, or throws answers 503 naming the
 * source — never the value. `keyFromFile` re-reads a file (a Secret the
 * kubelet refreshes in place) at most once per `ttlMs`. The file route uses
 * the same resolution. Runs on both express majors.
 */

describe('wipProxy getApiKey', () => {
  let upstream: Server
  let upstreamUrl: string
  let server: Server | undefined
  let base: string
  let dir: string
  const keys: (string | undefined)[] = []

  beforeEach(async () => {
    keys.length = 0
    dir = mkdtempSync(join(tmpdir(), 'wip-proxy-provider-'))
    upstream = createServer((req, res) => {
      keys.push(req.headers['x-api-key'] as string | undefined)
      res.setHeader('content-type', 'application/json')
      res.end('{"ok":true}')
    })
    await new Promise<void>((resolve) => upstream.listen(0, resolve))
    const a = upstream.address()
    if (a === null || typeof a === 'string') throw new Error('no upstream port')
    upstreamUrl = `http://127.0.0.1:${a.port}`
  })

  afterEach(async () => {
    if (server) await new Promise((resolve) => server!.close(resolve))
    server = undefined
    await new Promise((resolve) => upstream.close(resolve))
    rmSync(dir, { recursive: true, force: true })
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  async function serve(options: Partial<WipProxyOptions>): Promise<WipProxyRouter> {
    const app = express()
    const proxy = wipProxy({ baseUrl: upstreamUrl, ...options })
    app.use('/wip', proxy)
    server = createServer(app)
    await new Promise<void>((resolve) => server!.listen(0, resolve))
    const a = server.address()
    if (a === null || typeof a === 'string') throw new Error('no port')
    base = `http://127.0.0.1:${a.port}`
    return proxy
  }

  it('a keyless provider answers 503, then forwards once a key exists', async () => {
    let key: string | null = null
    const proxy = await serve({ getApiKey: () => key })

    const first = await fetch(`${base}/wip/api/registry/namespaces`)
    expect(first.status).toBe(503)
    const body = (await first.json()) as { error: string; error_code: string }
    expect(body.error_code).toBe('wip_proxy_unavailable')
    expect(body.error).toContain('getApiKey')
    expect(proxy.unavailableReason).toContain('getApiKey')

    key = 'live-key'
    const second = await fetch(`${base}/wip/api/registry/namespaces`)
    expect(second.status).toBe(200)
    expect(keys).toEqual(['live-key'])
    expect(proxy.unavailableReason).toBeNull()
  })

  it('a changed provider value is injected on the next request', async () => {
    let key = 'key-one'
    await serve({ getApiKey: async () => key })

    await fetch(`${base}/wip/api/registry/namespaces`)
    key = 'key-two'
    await fetch(`${base}/wip/api/def-store/terminologies`)

    expect(keys).toEqual(['key-one', 'key-two'])
  })

  it('the file route resolves the key the same way', async () => {
    let key: string | undefined
    await serve({ getApiKey: () => key })

    expect((await fetch(`${base}/wip/files/F-1/content`)).status).toBe(503)
    key = 'file-key'
    expect((await fetch(`${base}/wip/files/F-1/content`)).status).toBe(200)
    expect(keys).toEqual(['file-key'])
  })

  it('a provider value is trimmed like a static key', async () => {
    await serve({ getApiKey: () => 'live-key\n' })

    expect((await fetch(`${base}/wip/api/registry/namespaces`)).status).toBe(200)
    expect(keys).toEqual(['live-key'])
  })

  it.each([[''], ['   '], ['two words'], ['ctl\u0001char']])(
    'a provider value %j answers 503 without echoing it',
    async (value) => {
      const proxy = await serve({ getApiKey: () => value })

      const res = await fetch(`${base}/wip/api/registry/namespaces`)

      expect(res.status).toBe(503)
      const text = await res.text()
      if (value.trim()) expect(text).not.toContain(value)
      expect(keys).toEqual([])
      expect(proxy.unavailableReason).toContain('getApiKey')
    },
  )

  it.each([
    ['throws', () => { throw new Error('vault said sk-SECRET-123') }],
    ['rejects', () => Promise.reject(new Error('vault said sk-SECRET-123'))],
  ])('a provider that %s answers 503 and logs no key', async (_how, provider) => {
    const logged: string[] = []
    for (const level of ['log', 'warn', 'error'] as const) {
      vi.spyOn(console, level).mockImplementation((...args) => logged.push(args.join(' ')))
    }
    await serve({ getApiKey: provider as unknown as () => string })

    const res = await fetch(`${base}/wip/api/registry/namespaces`)

    expect(res.status).toBe(503)
    expect(await res.text()).not.toContain('sk-SECRET-123')
    expect(logged.join('\n')).not.toContain('sk-SECRET-123')
    // and the proxy keeps serving
    expect((await fetch(`${base}/wip/api/registry/namespaces`)).status).toBe(503)
  })

  it('getApiKey is the only source when given; a static key alongside it is warned about, never used', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await serve({ getApiKey: () => null, apiKey: 'static-key' })

    expect(warn.mock.calls.map((c) => c.join(' ')).join('\n')).toMatch(/getApiKey.*apiKey/)
    expect((await fetch(`${base}/wip/api/registry/namespaces`)).status).toBe(503)
    expect(keys).toEqual([])
  })

  it("onMissingKey 'throw' does not throw for a provider: a provider is asked per request", async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    await expect(serve({ getApiKey: () => null, onMissingKey: 'throw' })).resolves.toBeDefined()
  })

  it('keyFromFile re-reads the file after ttlMs', async () => {
    const path = join(dir, 'api-key')
    writeFileSync(path, 'first-key\n')
    let now = 1_000_000
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    await serve({ getApiKey: keyFromFile(path, { ttlMs: 10_000 }) })

    await fetch(`${base}/wip/api/registry/namespaces`)
    writeFileSync(path, 'second-key\n')
    now += 5_000
    await fetch(`${base}/wip/api/registry/namespaces`)
    now += 6_000
    await fetch(`${base}/wip/api/registry/namespaces`)

    expect(keys).toEqual(['first-key', 'first-key', 'second-key'])
  })

  it('keyFromFile: a missing file answers 503 naming the path, and recovers when it appears', async () => {
    const path = join(dir, 'later-key')
    await serve({ getApiKey: keyFromFile(path, { ttlMs: 0 }) })

    const res = await fetch(`${base}/wip/api/registry/namespaces`)
    expect(res.status).toBe(503)
    expect(((await res.json()) as { error: string }).error).toContain(path)

    writeFileSync(path, 'arrived-key')
    expect((await fetch(`${base}/wip/api/registry/namespaces`)).status).toBe(200)
    expect(keys).toEqual(['arrived-key'])
  })

  it('a static apiKey still works exactly as before', async () => {
    const proxy = await serve({ apiKey: 'static-key' })

    expect((await fetch(`${base}/wip/api/registry/namespaces`)).status).toBe(200)
    expect(keys).toEqual(['static-key'])
    expect(proxy.unavailableReason).toBeNull()
  })
})
