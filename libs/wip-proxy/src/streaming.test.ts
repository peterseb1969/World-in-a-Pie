import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { createServer, type Server, type ServerResponse } from 'node:http'
import express from 'express'
import { wipProxy } from './index.js'

/**
 * Streaming contract (fixed after the RC measurements: buffered
 * proxying peaked at ~3x an 808MB archive; a real pipe stayed flat).
 *
 * A memory-flatness assertion doesn't fit vitest; the honest substitute is
 * interleaving: bytes must arrive at one end BEFORE the other end finishes
 * sending. Any buffered implementation fails these by construction, because
 * buffering means nothing is forwarded until the source has ended.
 */

describe('wipProxy streaming', () => {
  let upstream: Server
  let server: Server
  let base: string
  let upstreamHandler: (req: import('node:http').IncomingMessage, res: ServerResponse) => void

  beforeEach(async () => {
    upstreamHandler = (_req, res) => res.end()
    upstream = createServer((req, res) => upstreamHandler(req, res))
    await new Promise<void>((resolve) => upstream.listen(0, resolve))
    const ua = upstream.address()
    if (ua === null || typeof ua === 'string') throw new Error('no upstream port')

    const app = express()
    app.use('/wip', wipProxy({ baseUrl: `http://127.0.0.1:${ua.port}`, apiKey: 'k' }))
    server = createServer(app)
    await new Promise<void>((resolve) => server.listen(0, resolve))
    const a = server.address()
    if (a === null || typeof a === 'string') throw new Error('no port')
    base = `http://127.0.0.1:${a.port}`
  })

  afterEach(async () => {
    await new Promise((resolve) => server.close(resolve))
    await new Promise((resolve) => upstream.close(resolve))
  })

  it('streams the response: first chunk reaches the client before the upstream body ends', async () => {
    let finishUpstream!: () => void
    upstreamHandler = (_req, res) => {
      res.setHeader('content-type', 'application/octet-stream')
      res.write('chunk-one|')
      finishUpstream = () => res.end('chunk-two')
    }

    const res = await fetch(`${base}/wip/api/document-store/documents`)
    expect(res.status).toBe(200)
    const reader = res.body!.getReader()

    // First chunk must be readable while the upstream response is still open.
    const first = await reader.read()
    expect(new TextDecoder().decode(first.value)).toContain('chunk-one|')

    finishUpstream()
    let rest = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      rest += new TextDecoder().decode(value)
    }
    expect(rest).toContain('chunk-two')
  })

  it('streams the request: first body chunk reaches the upstream before the client ends', async () => {
    let sawFirstChunk!: (chunk: string) => void
    const firstChunkAtUpstream = new Promise<string>((resolve) => { sawFirstChunk = resolve })
    let bodyAtUpstream = ''
    let upstreamDone!: (body: string) => void
    const upstreamBody = new Promise<string>((resolve) => { upstreamDone = resolve })

    upstreamHandler = (req, res) => {
      req.on('data', (chunk: Buffer) => {
        if (!bodyAtUpstream) sawFirstChunk(chunk.toString())
        bodyAtUpstream += chunk.toString()
      })
      req.on('end', () => {
        res.end('{}')
        upstreamDone(bodyAtUpstream)
      })
    }

    // A pull-based request body: the second chunk is released only after the
    // first has been OBSERVED at the upstream — impossible under buffering.
    let releaseSecondChunk!: () => void
    const gate = new Promise<void>((resolve) => { releaseSecondChunk = resolve })
    const body = new ReadableStream({
      async start(controller) {
        controller.enqueue(new TextEncoder().encode('part-one|'))
        await gate
        controller.enqueue(new TextEncoder().encode('part-two'))
        controller.close()
      },
    })

    const resPromise = fetch(`${base}/wip/api/document-store/documents`, {
      method: 'POST',
      headers: { 'content-type': 'application/octet-stream' },
      body,
      duplex: 'half',
    } as RequestInit & { duplex: 'half' })

    const first = await firstChunkAtUpstream
    expect(first).toContain('part-one|')
    releaseSecondChunk()

    const res = await resPromise
    expect(res.status).toBe(200)
    expect(await upstreamBody).toBe('part-one|part-two')
  })

  it('round-trips a request body intact without any body-parsing middleware', async () => {
    let received = ''
    upstreamHandler = (req, res) => {
      req.on('data', (c: Buffer) => { received += c.toString() })
      req.on('end', () => {
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify({ ok: true }))
      })
    }

    const payload = JSON.stringify([{ deep: { value: 'x'.repeat(4096) } }])
    const res = await fetch(`${base}/wip/api/document-store/documents`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: payload,
    })
    expect(res.status).toBe(200)
    expect(received).toBe(payload)
  })

  it('returns 502 JSON when the upstream is unreachable (error before headers)', async () => {
    // Point a fresh proxy at a port that nothing listens on.
    const dead = createServer(() => {})
    await new Promise<void>((resolve) => dead.listen(0, resolve))
    const da = dead.address()
    if (da === null || typeof da === 'string') throw new Error('no port')
    const deadPort = da.port
    await new Promise((resolve) => dead.close(resolve))

    const app = express()
    app.use('/wip', wipProxy({ baseUrl: `http://127.0.0.1:${deadPort}`, apiKey: 'k' }))
    const s = createServer(app)
    await new Promise<void>((resolve) => s.listen(0, resolve))
    const a = s.address()
    if (a === null || typeof a === 'string') throw new Error('no port')

    try {
      const res = await fetch(`http://127.0.0.1:${a.port}/wip/api/registry/namespaces`)
      expect(res.status).toBe(502)
      expect(await res.json()).toEqual({ error: 'Upstream request failed' })
    } finally {
      await new Promise((resolve) => s.close(resolve))
    }
  })

  it('follows redirects server-side on the file-content route', async () => {
    upstreamHandler = (req, res) => {
      if (req.url?.includes('/files/F-1/content')) {
        res.statusCode = 302
        res.setHeader('location', '/internal-minio/blob-1')
        res.end()
        return
      }
      if (req.url === '/internal-minio/blob-1') {
        res.setHeader('content-type', 'application/zip')
        res.end('blob-bytes')
        return
      }
      res.statusCode = 404
      res.end()
    }

    const res = await fetch(`${base}/wip/files/F-1/content`)
    expect(res.status).toBe(200)
    expect(await res.text()).toBe('blob-bytes')
    expect(res.headers.get('content-type')).toBe('application/zip')
  })

  it('forwards redirects to the client on the API routes (no body to replay)', async () => {
    upstreamHandler = (_req, res) => {
      res.statusCode = 307
      res.setHeader('location', '/api/registry/namespaces/')
      res.end()
    }

    const res = await fetch(`${base}/wip/api/registry/namespaces`, { redirect: 'manual' })
    expect(res.status).toBe(307)
  })
})
