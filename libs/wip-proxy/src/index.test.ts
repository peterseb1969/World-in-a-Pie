import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import { writeFileSync, mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { resolveApiKey } from './index.js'

describe('resolveApiKey (CASE-495 — key file source)', () => {
  let dir: string
  let keyFile: string
  let emptyFile: string

  beforeAll(() => {
    dir = mkdtempSync(join(tmpdir(), 'wip-proxy-'))
    keyFile = join(dir, 'api-key')
    emptyFile = join(dir, 'empty-key')
    // trailing newline is intentional — the resolver must trim it
    writeFileSync(keyFile, 'live-secret-key\n')
    writeFileSync(emptyFile, '   \n')
  })

  afterAll(() => {
    rmSync(dir, { recursive: true, force: true })
  })

  it('reads and trims the key from apiKeyFile', () => {
    expect(resolveApiKey({ baseUrl: 'x', apiKeyFile: keyFile })).toBe('live-secret-key')
  })

  it('apiKeyFile takes precedence over a literal apiKey', () => {
    expect(resolveApiKey({ baseUrl: 'x', apiKey: 'literal', apiKeyFile: keyFile })).toBe(
      'live-secret-key',
    )
  })

  it('falls back to the literal apiKey when no file is given', () => {
    expect(resolveApiKey({ baseUrl: 'x', apiKey: 'literal' })).toBe('literal')
  })

  it('throws when neither apiKey nor apiKeyFile is provided', () => {
    expect(() => resolveApiKey({ baseUrl: 'x' })).toThrow(/one of apiKey or apiKeyFile/)
  })

  it('throws (loud, at construction) when the key file is empty/whitespace', () => {
    expect(() => resolveApiKey({ baseUrl: 'x', apiKeyFile: emptyFile })).toThrow(/is empty/)
  })

  it('throws when the key file does not exist', () => {
    expect(() => resolveApiKey({ baseUrl: 'x', apiKeyFile: join(dir, 'nope') })).toThrow()
  })
})
