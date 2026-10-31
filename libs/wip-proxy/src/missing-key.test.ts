import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createServer, type Server } from 'node:http'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import express from 'express'
import { wipProxy, resolveApiKey, type WipProxyOptions } from './index.js'

/**
 * `onMissingKey: 'unavailable'` (CASE-1007): with no key, the proxy's routes
 * answer 503 naming the reason instead of `wipProxy()` throwing at module load
 * and taking the whole app, health endpoint included, down before `listen()`.
 * The default stays `'throw'`. Runs on both express majors (vitest projects).
 */

describe('wipProxy onMissingKey', () => {
  let server: Server | undefined
  let base: string
  let dir: string
  const upstreamCalls: string[] = []
  let upstream: Server
  let upstreamUrl: string

  beforeEach(async () => {
    dir = mkdtempSync(join(tmpdir(), 'wip-proxy-missing-'))
    upstreamCalls.length = 0
    upstream = createServer((req, res) => {
      upstreamCalls.push(req.url ?? '')
      res.setHeader('content-type', 'application/json')
      res.end('{"ok":true}')
    })
    await new Promise<void>((resolve) => upstream.listen(0, resolve))
    const address = upstream.address()
    if (address === null || typeof address === 'string') throw new Error('no upstream port')
    upstreamUrl = `http://127.0.0.1:${address.port}`
  })

  afterEach(async () => {
    if (server) await new Promise((resolve) => server!.close(resolve))
    server = undefined
    await new Promise((resolve) => upstream.close(resolve))
    rmSync(dir, { recursive: true, force: true })
    vi.restoreAllMocks()
  })

  async function serve(options: Partial<WipProxyOptions>) {
    const app = express()
    const proxy = wipProxy({ baseUrl: upstreamUrl, ...options })
    app.use('/wip', proxy)
    app.use(express.json())
    app.post('/wip/echo', (req, res) => res.json({ body: req.body }))
    app.get('/api/health', (_req, res) => res.json({ status: 'healthy' }))
    server = createServer(app)
    await new Promise<void>((resolve) => server!.listen(0, resolve))
    const address = server.address()
    if (address === null || typeof address === 'string') throw new Error('no port')
    base = `http://127.0.0.1:${address.port}`
    return proxy
  }

  it('throws at construction by default, as before', () => {
    expect(() => wipProxy({ baseUrl: upstreamUrl })).toThrow(
      'wipProxy: one of apiKey or apiKeyFile is required',
    )
  })

  it("with 'unavailable' and no key, the app still serves; proxied routes answer 503", async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const proxy = await serve({ onMissingKey: 'unavailable' })

    expect((await fetch(`${base}/api/health`)).status).toBe(200)
    for (const path of ['/wip/api/registry/namespaces', '/wip/files/abc/content']) {
      const res = await fetch(`${base}${path}`)
      expect(res.status, path).toBe(503)
      expect(await res.json()).toEqual({
        error: 'wipProxy: one of apiKey or apiKeyFile is required',
        error_code: 'wip_proxy_unavailable',
      })
    }
    expect(upstreamCalls).toEqual([])
    expect(proxy.unavailableReason).toBe('wipProxy: one of apiKey or apiKeyFile is required')
    expect(warn).toHaveBeenCalledOnce()
    expect(String(warn.mock.calls[0][0])).toContain('wipProxy: one of apiKey or apiKeyFile is required')
  })

  it('the 503 reason is exactly the message the throw would carry', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const options = { apiKeyFile: join(dir, 'missing-key') }
    let thrown = ''
    try {
      resolveApiKey({ baseUrl: upstreamUrl, ...options })
    } catch (err) {
      thrown = (err as Error).message
    }
    expect(thrown).toContain('is unreadable')

    await serve({ ...options, onMissingKey: 'unavailable' })
    const res = await fetch(`${base}/wip/api/def-store/terminologies`)
    expect(((await res.json()) as { error: string }).error).toBe(thrown)
  })

  it('paths the proxy never served still fall through to the app, body intact', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    await serve({ onMissingKey: 'unavailable' })
    const res = await fetch(`${base}/wip/echo`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ a: 1 }),
    })
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ body: { a: 1 } })
  })

  it('an unreadable file with an inline key proxies on the fallback, not 503', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const proxy = await serve({
      apiKeyFile: join(dir, 'missing-key'), apiKey: 'inline', onMissingKey: 'unavailable',
    })
    const res = await fetch(`${base}/wip/api/registry/namespaces`)
    expect(res.status).toBe(200)
    expect(upstreamCalls).toEqual(['/api/registry/namespaces'])
    expect(proxy.unavailableReason).toBeNull()
    expect(String(warn.mock.calls[0][0])).toContain('falling back to the inline apiKey')
  })

  it('a valid key proxies the same with either setting', async () => {
    for (const onMissingKey of ['throw', 'unavailable'] as const) {
      const proxy = await serve({ apiKey: 'k', onMissingKey })
      expect((await fetch(`${base}/wip/api/registry/namespaces`)).status).toBe(200)
      expect(proxy.unavailableReason).toBeNull()
      await new Promise((resolve) => server!.close(resolve))
      server = undefined
    }
    expect(upstreamCalls).toEqual(['/api/registry/namespaces', '/api/registry/namespaces'])
  })
})
