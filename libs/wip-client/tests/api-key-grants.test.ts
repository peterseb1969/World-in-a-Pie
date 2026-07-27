import { describe, it, expect } from 'vitest'
import type { APIKeyInfo } from '../src/types/registry'

// CASE-702 — APIKeyInfo must carry a typed `grants` field mirroring the
// Registry's api-keys metadata (CASE-693). These objects are compile-time
// guards: if `grants` were missing from the type, the file would not compile.
describe('APIKeyInfo.grants (CASE-702)', () => {
  it('accepts config-declared grants', () => {
    const key: APIKeyInfo = {
      name: 'web-yac',
      owner: 'system',
      groups: [],
      description: null,
      created_at: '2026-07-18T00:00:00Z',
      expires_at: null,
      enabled: true,
      namespaces: ['library', 'kb'],
      created_by: 'config-file',
      source: 'config',
      grants: { kb: 'write' },
    }
    expect(key.grants).toEqual({ kb: 'write' })
  })

  it('accepts null grants (runtime keys)', () => {
    const key: APIKeyInfo = {
      name: 'temp',
      owner: 'system',
      groups: [],
      description: null,
      created_at: '2026-07-18T00:00:00Z',
      expires_at: null,
      enabled: true,
      namespaces: null,
      created_by: 'admin',
      source: 'runtime',
      grants: null,
    }
    expect(key.grants).toBeNull()
  })
})
