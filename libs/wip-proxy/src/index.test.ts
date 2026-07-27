import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest'
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

describe('resolveApiKey (CASE-714 — graceful fallback to the inline key)', () => {
  let dir: string
  let emptyFile: string

  beforeAll(() => {
    dir = mkdtempSync(join(tmpdir(), 'wip-proxy-714-'))
    emptyFile = join(dir, 'empty-key')
    writeFileSync(emptyFile, '\n')
  })

  afterAll(() => {
    rmSync(dir, { recursive: true, force: true })
  })

  it('falls back to apiKey when apiKeyFile does not exist, with a warning', () => {
    // The containerized-dev shape: a bind-mounted .env names a host path
    // that does not exist in the container, while the deployer injected a
    // valid inline key into the environment.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const key = resolveApiKey({
        baseUrl: 'x',
        apiKey: 'injected-env-key',
        apiKeyFile: join(dir, 'not-mounted-here'),
      })
      expect(key).toBe('injected-env-key')
      expect(warn).toHaveBeenCalledOnce()
      expect(warn.mock.calls[0][0]).toMatch(/unreadable/)
      expect(warn.mock.calls[0][0]).toMatch(/falling back to the inline apiKey/)
    } finally {
      warn.mockRestore()
    }
  })

  it('falls back to apiKey when apiKeyFile is empty, with a warning', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const key = resolveApiKey({
        baseUrl: 'x',
        apiKey: 'injected-env-key',
        apiKeyFile: emptyFile,
      })
      expect(key).toBe('injected-env-key')
      expect(warn.mock.calls[0][0]).toMatch(/is empty/)
    } finally {
      warn.mockRestore()
    }
  })

  it('still fails loudly when the file fails and no inline key exists — naming both attempts', () => {
    expect(() =>
      resolveApiKey({ baseUrl: 'x', apiKeyFile: join(dir, 'nope') }),
    ).toThrow(/unreadable.*no inline apiKey/s)
  })
})
