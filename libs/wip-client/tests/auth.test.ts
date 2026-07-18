import { describe, it, expect, vi } from 'vitest'
import { ApiKeyAuthProvider, OidcAuthProvider } from '../src/auth/index'

describe('auth providers — cacheable contract (CASE-569)', () => {
  it('ApiKeyAuthProvider is cacheable (static credential)', () => {
    const p = new ApiKeyAuthProvider('k')
    expect(p.cacheable).toBe(true)
    expect(p.getHeaders()).toEqual({ 'X-API-Key': 'k' })
  })

  it('OidcAuthProvider is NOT cacheable and re-invokes getToken each call', async () => {
    let n = 0
    const p = new OidcAuthProvider(() => `t-${++n}`)
    // Not opted into caching — the transport must re-fetch every request.
    expect(p.cacheable).toBeFalsy()
    expect(await p.getHeaders()).toEqual({ Authorization: 'Bearer t-1' })
    expect(await p.getHeaders()).toEqual({ Authorization: 'Bearer t-2' })
  })
})
