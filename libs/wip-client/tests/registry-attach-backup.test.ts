import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createWipClient } from '../src/client'
import type { WipClient } from '../src/client'
import { WipConflictError, WipValidationError, wipErrorCode } from '../src/errors'
import { MANAGED_BY_DEPLOYER, PLAN_CHANGED, RESTORE_REFUSED, SALT_MISMATCH } from '../src/types/registry'
import type { SystemArchive } from '../src/types/registry'

// The Registry surfaces RC builds its attach, system-backup and orphaned-grant
// pages on: attachments, the system backup and restore, orphaned grants.

describe('RegistryService: attachments, system backup, orphaned grants', () => {
  let client: WipClient
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    client = createWipClient({ baseUrl: 'http://localhost', auth: { type: 'api-key', key: 'test-key' } })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function respond(data: unknown, status = 200) {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }),
    )
  }

  function call(i = 0): { url: string; method: string; body: unknown } {
    const [url, init] = fetchMock.mock.calls[i]
    return { url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined }
  }

  const ARCHIVE: SystemArchive = {
    format: 'wip-system-backup',
    version: 1,
    created_at: '2026-11-04T00:00:00Z',
    source: { oidc_issuer: null, salt_fingerprint: 'sha256:x', salt: null },
    api_keys: [],
    grants: [],
    reservations: [],
    attachments: [],
  }

  describe('system backup and restore', () => {
    it('systemBackup POSTs and returns the archive', async () => {
      respond(ARCHIVE)
      const archive = await client.registry.systemBackup()
      expect(archive.format).toBe('wip-system-backup')
      expect(call()).toMatchObject({ method: 'POST' })
      expect(call().url).toContain('/api/registry/system-backup')
    })

    it('systemBackup with a scope sends it as comma lists', async () => {
      respond(ARCHIVE)
      await client.registry.systemBackup({ namespaces: ['aa', 'aa-data'], attachments: ['aa'] })
      const { url } = call()
      expect(url).toContain('namespaces=aa%2Caa-data')
      expect(url).toContain('attachments=aa')
    })

    it('systemBackup without options sends no scope', async () => {
      respond(ARCHIVE)
      await client.registry.systemBackup()
      expect(call().url).not.toContain('namespaces=')
    })

    it('systemRestore is a dry run by default and sends the archive as the body', async () => {
      respond({ dry_run: true, salt_adopted: false, warnings: [], reservations: {}, api_keys: {}, grants: {}, attachments: {} })
      await client.registry.systemRestore(ARCHIVE)
      const { url, body } = call()
      expect(url).toContain('/api/registry/system-restore')
      expect(url).toContain('dry_run=true')
      expect(url).toContain('overwrite=false')
      expect(url).toContain('include_user_grants=false')
      expect(body).toEqual(ARCHIVE)
    })

    it('systemRestore passes its options', async () => {
      respond({ dry_run: false })
      await client.registry.systemRestore(ARCHIVE, { dryRun: false, overwrite: true, includeUserGrants: true })
      const { url } = call()
      expect(url).toContain('dry_run=false')
      expect(url).toContain('overwrite=true')
      expect(url).toContain('include_user_grants=true')
    })

    it('a mirror sends mode and expect, and no overwrite of its own', async () => {
      respond({ dry_run: false, mode: 'mirror' })
      await client.registry.systemRestore(ARCHIVE, { dryRun: false, mode: 'mirror', expect: 'sha256:abc' })
      const { url } = call()
      expect(url).toContain('mode=mirror')
      expect(url).toContain('expect=sha256%3Aabc')
      expect(url).not.toContain('overwrite=')
    })

    it('a refused restore and a changed plan are typed 409s', async () => {
      respond({ detail: 'would remove the calling key', error_code: 'restore_refused' }, 409)
      const refused = await client.registry.systemRestore(ARCHIVE, { mode: 'mirror', dryRun: false }).catch((e) => e)
      expect(wipErrorCode(refused)).toBe(RESTORE_REFUSED)
      respond({ detail: 'the plan changed', error_code: 'plan_changed' }, 409)
      const changed = await client.registry.systemRestore(ARCHIVE, { mode: 'mirror', dryRun: false, expect: 'x' }).catch((e) => e)
      expect(wipErrorCode(changed)).toBe(PLAN_CHANGED)
    })

    it('a salt refusal is a WipConflictError whose code is SALT_MISMATCH', async () => {
      respond({ detail: 'another salt', error_code: 'salt_mismatch' }, 409)
      const err = await client.registry.systemRestore(ARCHIVE).catch((e) => e)
      expect(err).toBeInstanceOf(WipConflictError)
      expect(wipErrorCode(err)).toBe(SALT_MISMATCH)
      expect(err.message).toBe('another salt')
    })

    it('wipErrorCode is undefined without a code, and for non-WIP errors', async () => {
      respond({ detail: 'plain' }, 409)
      const err = await client.registry.systemRestore(ARCHIVE).catch((e) => e)
      expect(wipErrorCode(err)).toBeUndefined()
      expect(wipErrorCode(new Error('x'))).toBeUndefined()
    })
  })

  describe('attachments', () => {
    it('upsertAttachments POSTs the items as a bulk list', async () => {
      respond({ results: [{ index: 0, status: 'created', name: 'aa', path: '/apps/aa' }], total: 1, succeeded: 1, failed: 0 })
      const res = await client.registry.upsertAttachments([
        { name: 'aa', upstream: 'https://aa.example', auth_required: true, allowed_groups: [] },
      ])
      expect(res.results[0].path).toBe('/apps/aa')
      expect(call()).toMatchObject({
        method: 'POST',
        body: [{ name: 'aa', upstream: 'https://aa.example', auth_required: true, allowed_groups: [] }],
      })
    })

    it('a partial record is a 422 for the whole request', async () => {
      respond({ detail: [{ loc: ['body', 0, 'auth_required'], msg: 'Field required' }] }, 422)
      const err = await client.registry
        .upsertAttachments([{ name: 'aa', upstream: 'https://aa.example' } as never])
        .catch((e) => e)
      expect(err).toBeInstanceOf(WipValidationError)
    })

    it('deactivateAttachments sends one item per name', async () => {
      respond({ results: [{ index: 0, status: 'deactivated', name: 'aa' }], total: 1, succeeded: 1, failed: 0 })
      await client.registry.deactivateAttachments(['aa', 'bb'])
      expect(call()).toMatchObject({ method: 'POST', body: [{ name: 'aa' }, { name: 'bb' }] })
      expect(call().url).toContain('/api/registry/attachments/deactivate')
    })

    it('listAttachments passes the filter and pagination', async () => {
      respond({ items: [], total: 0, page: 2, page_size: 10, pages: 0 })
      await client.registry.listAttachments({ status: 'inactive', page: 2, page_size: 10 })
      const { url } = call()
      expect(url).toContain('/api/registry/attachments?')
      expect(url).toContain('status=inactive')
      expect(url).toContain('page=2')
    })

    it('getAttachment reads one by name', async () => {
      respond({ name: 'aa', path: '/apps/aa' })
      await client.registry.getAttachment('aa')
      expect(call().url).toContain('/api/registry/attachments/aa')
    })
  })

  describe('orphaned grants', () => {
    it('activateGrants POSTs to the namespace activate route', async () => {
      respond({ results: [{ index: 0, status: 'activated', subject: 'k' }], total: 1, succeeded: 1, failed: 0 })
      const res = await client.registry.activateGrants('ns-a', [{ subject: 'k', subject_type: 'api_key' }])
      expect(res.results[0].status).toBe('activated')
      expect(call()).toMatchObject({ method: 'POST', body: [{ subject: 'k', subject_type: 'api_key' }] })
      expect(call().url).toContain('/api/registry/namespaces/ns-a/grants/activate')
    })

    it('listAllGrants filters orphans across namespaces', async () => {
      respond({ items: [{ namespace: 'gone', subject: 'k', orphaned: true }], total: 1, page: 1, page_size: 50, pages: 1 })
      const res = await client.registry.listAllGrants({ orphaned: true })
      expect(res.items[0].orphaned).toBe(true)
      expect(call().url).toContain('/api/registry/grants?')
      expect(call().url).toContain('orphaned=true')
    })

    it('the managed-record code is still exported beside the new one', () => {
      expect(MANAGED_BY_DEPLOYER).toBe('managed_by_deployer')
      expect(SALT_MISMATCH).toBe('salt_mismatch')
    })
  })
})
