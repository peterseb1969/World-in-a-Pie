import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { createServer, request, type IncomingMessage, type Server, type ServerResponse } from 'node:http'
import express from 'express'
import { wipProxy } from './index.js'

/**
 * What leaves the proxy is what the allowlist allowed, and the key goes only
 * where WIP is.
 *
 * - The upstream path is checked AFTER URL normalisation: dot segments
 *   (literal, percent-encoded, backslashed) that would carry a request out of
 *   the six service prefixes — or sideways into a look-alike prefix — are
 *   refused with 400 and never reach WIP.
 * - A file id is one path segment: an encoded `/` stays encoded upstream.
 * - The file proxy follows a redirect to another origin without the key, and
 *   a malformed Location is a 502, not a crash.
 * - A client's X-WIP-User / X-WIP-Groups never reach WIP, on the API path or
 *   the file path: the proxy cannot tell who set them, and under a privileged
 *   key a service would act on them as the logged-in user.
 * - An API redirect reaches the client with a Location it can follow: a WIP
 *   path is rewritten under the proxy's mount; anything else is dropped.
 *
 * Requests are sent with node:http so the path reaches the proxy exactly as
 * written — fetch() would collapse the dot segments client-side.
 */

type Handler = (req: IncomingMessage, res: ServerResponse) => void

async function listen(handler: Handler): Promise<{ server: Server; url: string }> {
  const server = createServer((req, res) => handler(req, res))
  await new Promise<void>((resolve) => server.listen(0, resolve))
  const a = server.address()
  if (a === null || typeof a === 'string') throw new Error('no port')
  return { server, url: `http://127.0.0.1:${a.port}` }
}

function raw(
  base: string,
  path: string,
  headers: Record<string, string> = {},
): Promise<{ status: number; headers: IncomingMessage['headers']; body: string }> {
  const { hostname, port } = new URL(base)
  return new Promise((resolve, reject) => {
    const req = request({ hostname, port, path, method: 'GET', headers }, (res) => {
      let body = ''
      res.on('data', (c) => (body += c))
      res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body }))
    })
    req.on('error', reject)
    req.end()
  })
}

describe('wipProxy upstream path and redirects', () => {
  let upstream: { server: Server; url: string }
  let other: { server: Server; url: string }
  let app: { server: Server; url: string }
  let upstreamHandler: Handler
  let otherHandler: Handler
  const seen: { path: string; key?: string; user?: string; groups?: string }[] = []
  const otherSeen: { path: string; key?: string; user?: string; groups?: string }[] = []

  beforeEach(async () => {
    seen.length = 0
    otherSeen.length = 0
    upstreamHandler = (_req, res) => res.end('ok')
    otherHandler = (_req, res) => res.end('from-other')
    upstream = await listen((req, res) => {
      seen.push({ path: req.url ?? '', key: req.headers['x-api-key'] as string, user: req.headers['x-wip-user'] as string, groups: req.headers['x-wip-groups'] as string })
      upstreamHandler(req, res)
    })
    other = await listen((req, res) => {
      otherSeen.push({ path: req.url ?? '', key: req.headers['x-api-key'] as string, user: req.headers['x-wip-user'] as string, groups: req.headers['x-wip-groups'] as string })
      otherHandler(req, res)
    })
    const e = express()
    e.use('/wip', wipProxy({ baseUrl: upstream.url, apiKey: 'secret-key' }))
    e.use((_req, res) => res.status(404).send('app 404'))
    app = await listen(e)
  })

  afterEach(async () => {
    for (const s of [app, upstream, other]) await new Promise((r) => s.server.close(r))
  })

  it.each([
    ['/wip/api/def-store/../../auth/userinfo'],
    ['/wip/api/def-store/../../../../mcp'],
    ['/wip/api/def-store/%2e%2e/%2e%2e/auth/userinfo'],
    ['/wip/api/def-store/%2E%2E/%2E%2E/auth/userinfo'],
    ['/wip/api/def-store/..\\..\\auth\\userinfo'],
    ['/wip/api/def-store/../def-store-evil/x'],
    ['/wip/api/registry/../templates'],
  ])('refuses %s without calling WIP', async (path) => {
    const res = await raw(app.url, path)

    expect(res.status).toBe(400)
    expect(JSON.parse(res.body).error_code).toBe('wip_proxy_path_refused')
    expect(seen).toEqual([])
  })

  it('a dot segment that stays inside a prefix is forwarded, normalised', async () => {
    const res = await raw(app.url, '/wip/api/def-store/terminologies/../terms?x=1')

    expect(res.status).toBe(200)
    expect(seen.map((s) => s.path)).toEqual(['/api/def-store/terms?x=1'])
  })

  it('an ordinary request is forwarded unchanged', async () => {
    const res = await raw(app.url, '/wip/api/def-store/terminologies?page=2')

    expect(res.status).toBe(200)
    expect(seen.map((s) => s.path)).toEqual(['/api/def-store/terminologies?page=2'])
  })

  it('a file id is one segment: an encoded slash stays encoded', async () => {
    await raw(app.url, '/wip/files/..%2F..%2F..%2Fauth%2Fuserinfo/content')

    expect(seen.map((s) => s.path)).toEqual([
      '/api/document-store/files/..%2F..%2F..%2Fauth%2Fuserinfo/content',
    ])
  })

  it('a client-sent identity is not forwarded, on the API path or the file path, even by an app still passing forwardIdentity', async () => {
    // An untyped (JS, tsx) caller can still hand over the removed option.
    const stale = { baseUrl: upstream.url, apiKey: 'secret-key', forwardIdentity: true } as Parameters<typeof wipProxy>[0]
    const e = express()
    e.use('/wip', wipProxy(stale))
    const staleApp = await listen(e)
    const forged = { 'x-wip-user': 'admin@wip.local', 'x-wip-groups': 'wip-admins' }
    try {
      expect((await raw(staleApp.url, '/wip/api/document-store/documents', forged)).status).toBe(200)
      expect((await raw(staleApp.url, '/wip/files/F-1/content', forged)).status).toBe(200)
    } finally {
      await new Promise((r) => staleApp.server.close(r))
    }

    expect(seen).toEqual([
      { path: '/api/document-store/documents', key: 'secret-key', user: undefined, groups: undefined },
      { path: '/api/document-store/files/F-1/content', key: 'secret-key', user: undefined, groups: undefined },
    ])
  })

  it('the file proxy follows a cross-origin redirect without the key', async () => {
    upstreamHandler = (_req, res) => {
      res.statusCode = 302
      res.setHeader('location', `${other.url}/bucket/blob?sig=1`)
      res.end()
    }

    const res = await new Promise<{ status: number; body: string }>((resolve, reject) => {
      const { hostname, port } = new URL(app.url)
      const req = request(
        { hostname, port, path: '/wip/files/F-1/content', headers: { 'x-wip-user': 'alice' } },
        (r) => {
          let body = ''
          r.on('data', (c) => (body += c))
          r.on('end', () => resolve({ status: r.statusCode ?? 0, body }))
        },
      )
      req.on('error', reject)
      req.end()
    })

    expect(res.status).toBe(200)
    expect(res.body).toBe('from-other')
    expect(seen[0].key).toBe('secret-key')
    expect(seen[0].user).toBeUndefined()
    expect(otherSeen).toEqual([{ path: '/bucket/blob?sig=1', key: undefined, user: undefined, groups: undefined }])
  })

  it('the file proxy keeps the key on a same-origin redirect', async () => {
    upstreamHandler = (req, res) => {
      if (req.url?.startsWith('/api/document-store/files/')) {
        res.statusCode = 307
        res.setHeader('location', '/blob/F-1')
        res.end()
      } else {
        res.end('same-origin-bytes')
      }
    }

    const res = await raw(app.url, '/wip/files/F-1/content')

    expect(res.body).toBe('same-origin-bytes')
    expect(seen.map((s) => s.key)).toEqual(['secret-key', 'secret-key'])
  })

  it('a malformed redirect Location is a 502, not a crash', async () => {
    upstreamHandler = (_req, res) => {
      res.statusCode = 302
      res.setHeader('location', 'http://[::1')
      res.end()
    }

    const res = await raw(app.url, '/wip/files/F-1/content')

    expect(res.status).toBe(502)
    // The proxy still serves the next request
    upstreamHandler = (_req, r) => r.end('ok')
    expect((await raw(app.url, '/wip/api/registry/namespaces')).status).toBe(200)
  })

  it.each([
    ['/api/registry/', '/wip/api/registry/'],
    ['/api/def-store/terms?page=2', '/wip/api/def-store/terms?page=2'],
  ])('an API redirect to %s reaches the client as %s', async (location, expected) => {
    upstreamHandler = (_req, res) => {
      res.statusCode = 307
      res.setHeader('location', location)
      res.end()
    }

    const res = await raw(app.url, '/wip/api/registry')

    expect(res.status).toBe(307)
    expect(res.headers.location).toBe(expected)
  })

  it('an absolute API redirect onto WIP is rewritten under the mount', async () => {
    upstreamHandler = (_req, res) => {
      res.statusCode = 308
      res.setHeader('location', `${upstream.url}/api/registry/`)
      res.end()
    }

    const res = await raw(app.url, '/wip/api/registry')

    expect(res.headers.location).toBe('/wip/api/registry/')
  })

  it.each([['https://elsewhere.example/x'], ['/auth/login'], ['/api/def-store/../../mcp']])(
    'an API redirect to %s is passed on without a Location',
    async (location) => {
      upstreamHandler = (_req, res) => {
        res.statusCode = 302
        res.setHeader('location', location)
        res.end()
      }

      const res = await raw(app.url, '/wip/api/registry/x')

      expect(res.status).toBe(302)
      expect(res.headers.location).toBeUndefined()
    },
  )
})
