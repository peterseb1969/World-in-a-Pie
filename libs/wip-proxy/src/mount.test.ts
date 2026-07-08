import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createServer, type Server } from 'node:http'
import express, { Router } from 'express'
import { wipProxy, prefixPattern, WIP_API_PREFIXES } from './index.js'

/**
 * Route registration + matching across both express peer majors. The vitest
 * config runs this file twice — once with `express` aliased to express 4,
 * once to express 5 — because their routers parse route patterns with
 * incompatible path-to-regexp generations: a pattern that registers fine on
 * one major can throw at mount time on the other, which is exactly how a
 * proxy that only ever ran on a bundled express 4 shipped an express-5 crash
 * behind a `^5.0.0` peer claim.
 */

const EXPECTED_MAJOR = process.env.WIP_PROXY_TEST_EXPRESS_MAJOR

describe(`express-major canary (expecting ${EXPECTED_MAJOR})`, () => {
  it('resolves the express major this project claims to test', () => {
    // Bare `*` registers on express 4 and throws on express 5 — a behavioural
    // fingerprint of the resolved major. If the vitest alias ever breaks and
    // both projects silently resolve the same express, this fails loudly.
    let threw = false
    try {
      Router().all('/canary/*', () => {})
    } catch {
      threw = true
    }
    expect(EXPECTED_MAJOR, 'WIP_PROXY_TEST_EXPRESS_MAJOR must be set by vitest.config.ts').toMatch(/^[45]$/)
    expect(threw).toBe(EXPECTED_MAJOR === '5')
  })
})

describe('prefixPattern', () => {
  it('matches the bare prefix and subpaths, anchored on both ends', () => {
    const re = prefixPattern('/api/def-store')
    expect(re.test('/api/def-store')).toBe(true)
    expect(re.test('/api/def-store/terminologies')).toBe(true)
    expect(re.test('/api/def-store/terminologies/T-1/terms')).toBe(true)
    expect(re.test('/api/def-store-evil')).toBe(false)
    expect(re.test('/api/def-storeX/y')).toBe(false)
    expect(re.test('/prefix/api/def-store')).toBe(false)
  })

  it('escapes regex metacharacters in the prefix', () => {
    const re = prefixPattern('/api/v1.0')
    expect(re.test('/api/v1.0/x')).toBe(true)
    expect(re.test('/api/v1X0/x')).toBe(false)
  })
})

describe('wipProxy mount + routing', () => {
  let server: Server
  let base: string
  const upstreamCalls: Array<{ url: string; method: string }> = []
  // The proxy handler calls the global fetch at request time, so the global
  // gets stubbed — the tests' own client requests must go through the real
  // one or they'd hit the stub instead of the HTTP server under test.
  const realFetch = globalThis.fetch
  const request = (path: string, init?: RequestInit) => realFetch(`${base}${path}`, init)

  beforeEach(async () => {
    upstreamCalls.length = 0
    // The suite under test is route registration and matching, not proxying —
    // stub the upstream fetch and record what the handler asked for.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string | URL, init?: RequestInit) => {
        upstreamCalls.push({ url: String(url), method: init?.method ?? 'GET' })
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )

    const app = express()
    app.use(
      '/wip',
      wipProxy({ baseUrl: 'https://wip.test', apiKey: 'k' }),
    )
    server = createServer(app)
    await new Promise<void>((resolve) => server.listen(0, resolve))
    const address = server.address()
    if (address === null || typeof address === 'string') throw new Error('no port')
    base = `http://127.0.0.1:${address.port}`
  })

  afterEach(async () => {
    vi.unstubAllGlobals()
    await new Promise((resolve) => server.close(resolve))
  })

  it('mounts without throwing on this express major', () => {
    // beforeEach already mounted — reaching here IS the regression assertion
    // (on express 5 the 0.4.1 pattern threw inside wipProxy() itself).
    expect(server.listening).toBe(true)
  })

  it('proxies a subpath with the query string intact', async () => {
    const res = await request(`/wip/api/def-store/terminologies?page=2&namespace=x%20y`)
    expect(res.status).toBe(200)
    expect(upstreamCalls).toEqual([
      {
        url: 'https://wip.test/api/def-store/terminologies?page=2&namespace=x%20y',
        method: 'GET',
      },
    ])
  })

  it('proxies the bare prefix (single registration covers it)', async () => {
    const res = await request(`/wip/api/registry`)
    expect(res.status).toBe(200)
    expect(upstreamCalls).toEqual([{ url: 'https://wip.test/api/registry', method: 'GET' }])
  })

  it('proxies every declared service prefix', async () => {
    for (const prefix of WIP_API_PREFIXES) {
      const res = await request(`/wip${prefix}/ping`)
      expect(res.status, prefix).toBe(200)
    }
    expect(upstreamCalls.map((c) => c.url)).toEqual(
      WIP_API_PREFIXES.map((p) => `https://wip.test${p}/ping`),
    )
  })

  it('forwards non-GET methods', async () => {
    const res = await request(`/wip/api/document-store/documents`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify([{ x: 1 }]),
    })
    expect(res.status).toBe(200)
    expect(upstreamCalls).toEqual([
      { url: 'https://wip.test/api/document-store/documents', method: 'POST' },
    ])
  })

  it('does not match sibling paths that merely start with a prefix string', async () => {
    const res = await request(`/wip/api/def-store-evil/x`)
    expect(res.status).toBe(404)
    expect(upstreamCalls).toEqual([])
  })

  it('leaves the file-content route reachable (named param registers on both majors)', async () => {
    const res = await request(`/wip/files/F-1/content`)
    // The file handler does its own upstream round-trips against the stub;
    // the assertion here is only that the route matched (no 404).
    expect(res.status).not.toBe(404)
  })
})
