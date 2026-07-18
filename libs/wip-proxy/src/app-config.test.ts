import { describe, it, expect, vi } from 'vitest'
import { appConfigHandler, type AppConfigEntries } from './app-config.js'

function invoke(entries: AppConfigEntries | (() => AppConfigEntries)) {
  const handler = appConfigHandler(entries)
  const res = {
    set: vi.fn().mockReturnThis(),
    json: vi.fn().mockReturnThis(),
  }
  handler({} as any, res as any)
  return res
}

describe('appConfigHandler (CASE-551)', () => {
  it('serves exactly the entries passed — allowlist by construction', () => {
    const res = invoke({ namespace: 'kb', library_namespace: 'library' })
    expect(res.json).toHaveBeenCalledWith({ namespace: 'kb', library_namespace: 'library' })
  })

  it('drops undefined entries (unset env reads) but serves explicit null', () => {
    const res = invoke({ namespace: null, flag: undefined, count: 0, on: false })
    expect(res.json).toHaveBeenCalledWith({ namespace: null, count: 0, on: false })
  })

  it('sets Cache-Control: no-store so deployment config is never pinned', () => {
    const res = invoke({ namespace: 'kb' })
    expect(res.set).toHaveBeenCalledWith('Cache-Control', 'no-store')
  })

  it('re-reads a function source per request (late-bound values)', () => {
    let ns = 'first'
    const handler = appConfigHandler(() => ({ namespace: ns }))
    const mkRes = () => ({ set: vi.fn().mockReturnThis(), json: vi.fn().mockReturnThis() })

    const res1 = mkRes()
    handler({} as any, res1 as any)
    expect(res1.json).toHaveBeenCalledWith({ namespace: 'first' })

    ns = 'rotated'
    const res2 = mkRes()
    handler({} as any, res2 as any)
    expect(res2.json).toHaveBeenCalledWith({ namespace: 'rotated' })
  })

  it('does not leak anything not explicitly passed (no env dump possible)', () => {
    process.env.WIP_API_KEY_TEST_CANARY = 'secret'
    try {
      const res = invoke({ namespace: 'kb' })
      const served = (res.json as any).mock.calls[0][0]
      expect(Object.keys(served)).toEqual(['namespace'])
    } finally {
      delete process.env.WIP_API_KEY_TEST_CANARY
    }
  })
})
