import { describe, it, expect, afterEach, vi } from 'vitest'
import { createServer, type Server } from 'node:http'
import { writeFileSync, mkdtempSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import express from 'express'
import { resolveApiKey, wipProxy } from './index.js'

/**
 * Key hygiene (CASE-1075): an inline key from an environment variable often
 * carries a trailing newline (a Secret written from `jq -r … > file`). Node
 * refuses `\n` and `\r` in a header value (ERR_INVALID_CHAR), so every
 * proxied call failed with 500. Both key sources are trimmed; a key with
 * whitespace or a control character inside it is refused at construction.
 * WIP keys are URL-safe base64 or hex and never contain either.
 */

describe('resolveApiKey trims and checks every key', () => {
  it.each(['k\n', 'k\r\n', ' k ', '\tk'])('trims the inline key %j', (raw) => {
    expect(resolveApiKey({ baseUrl: 'x', apiKey: raw })).toBe('k')
  })

  it('treats an inline key that is only whitespace as missing', () => {
    expect(() => resolveApiKey({ baseUrl: 'x', apiKey: ' \n ' })).toThrow(
      /one of apiKey or apiKeyFile is required/,
    )
  })

  it('falls back to the file when the inline key is only whitespace', () => {
    const dir = mkdtempSync(join(tmpdir(), 'wip-proxy-hygiene-'))
    const file = join(dir, 'key')
    writeFileSync(file, 'from-file\n')
    expect(resolveApiKey({ baseUrl: 'x', apiKey: '\n', apiKeyFile: file })).toBe('from-file')
  })

  it.each(['a\nb', 'a b', 'a\u0000b'])('refuses an inline key with %j inside', (raw) => {
    expect(() => resolveApiKey({ baseUrl: 'x', apiKey: raw })).toThrow(
      /apiKey contains whitespace or a control character/,
    )
  })

  it('refuses a file key with a newline inside', () => {
    const dir = mkdtempSync(join(tmpdir(), 'wip-proxy-hygiene-'))
    const file = join(dir, 'key')
    writeFileSync(file, 'first\nsecond\n')
    expect(() => resolveApiKey({ baseUrl: 'x', apiKeyFile: file })).toThrow(
      /apiKeyFile .* contains whitespace or a control character/,
    )
  })

  it("gives the unavailable proxy for such a key with onMissingKey: 'unavailable'", () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const proxy = wipProxy({ baseUrl: 'x', apiKey: 'a\nb', onMissingKey: 'unavailable' })
      expect(proxy.unavailableReason).toMatch(/whitespace or a control character/)
    } finally {
      warn.mockRestore()
    }
  })
})

describe('the proxied request carries the trimmed key', () => {
  let upstream: Server
  let server: Server

  afterEach(async () => {
    await new Promise((resolve) => server.close(resolve))
    await new Promise((resolve) => upstream.close(resolve))
  })

  it("sends X-API-Key without the trailing newline, instead of a 500", async () => {
    let seen: string | undefined
    upstream = createServer((req, res) => {
      seen = req.headers['x-api-key'] as string | undefined
      res.setHeader('Content-Type', 'application/json')
      res.end('{}')
    })
    await new Promise<void>((resolve) => upstream.listen(0, resolve))
    const ua = upstream.address()
    if (ua === null || typeof ua === 'string') throw new Error('no upstream port')

    const app = express()
    app.use('/wip', wipProxy({ baseUrl: `http://127.0.0.1:${ua.port}`, apiKey: 'live-key\n' }))
    server = createServer(app)
    await new Promise<void>((resolve) => server.listen(0, resolve))
    const a = server.address()
    if (a === null || typeof a === 'string') throw new Error('no port')

    const r = await fetch(`http://127.0.0.1:${a.port}/wip/api/registry/namespaces`)
    expect(r.status).toBe(200)
    expect(seen).toBe('live-key')
  })
})
