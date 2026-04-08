import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createWipClient } from '../src/client'
import type { WipClient } from '../src/client'
import { WipBulkItemError } from '../src/errors'
import type { BackupProgressMessage as BackupProgressMessageType } from '../src/types/backup'

describe('Service classes via createWipClient', () => {
  let client: WipClient
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    client = createWipClient({
      baseUrl: 'http://localhost',
      auth: { type: 'api-key', key: 'test-key' },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mockJsonResponse(data: unknown, status = 200) {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(data), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  describe('DefStoreService', () => {
    it('listTerminologies sends GET with params', async () => {
      mockJsonResponse({ items: [], total: 0, page: 1, page_size: 50, pages: 0 })

      const result = await client.defStore.listTerminologies({ page: 1, status: 'active' })

      expect(result.items).toEqual([])
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/def-store/terminologies')
      expect(url).toContain('page=1')
      expect(url).toContain('status=active')
    })

    it('createTerminology sends bulk POST and unwraps', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'created', id: '0190b000-0000-7000-0000-000000000001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      const result = await client.defStore.createTerminology({
        value: 'GENDER',
        label: 'Gender',
      })

      expect(result.status).toBe('created')
      expect(result.id).toBe('0190b000-0000-7000-0000-000000000001')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/def-store/terminologies')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ value: 'GENDER', label: 'Gender' }])
    })

    it('createTerminology throws WipBulkItemError on error result', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'error', error: 'Duplicate value' }],
        total: 1,
        succeeded: 0,
        failed: 1,
      })

      await expect(
        client.defStore.createTerminology({ value: 'GENDER', label: 'Gender' }),
      ).rejects.toThrow(WipBulkItemError)
    })

    it('deleteTerminology sends DELETE with body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'deleted', id: '0190b000-0000-7000-0000-000000000001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.defStore.deleteTerminology('0190b000-0000-7000-0000-000000000001')

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      expect(JSON.parse(options.body)).toEqual([{ id: '0190b000-0000-7000-0000-000000000001' }])
    })
  })

  describe('TemplateStoreService', () => {
    it('listTemplates sends GET with pagination params', async () => {
      mockJsonResponse({ items: [], total: 0, page: 1, page_size: 50, pages: 0 })

      const result = await client.templates.listTemplates({ page: 2, status: 'active', latest_only: true })

      expect(result.items).toEqual([])
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates')
      expect(url).toContain('page=2')
      expect(url).toContain('status=active')
      expect(url).toContain('latest_only=true')
    })

    it('getTemplate fetches by ID', async () => {
      mockJsonResponse({ template_id: '0190c000-0000-7000-0000-000000000001', value: 'TEST', version: 1 })

      const result = await client.templates.getTemplate('0190c000-0000-7000-0000-000000000001')

      expect(result.template_id).toBe('0190c000-0000-7000-0000-000000000001')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/0190c000-0000-7000-0000-000000000001')
    })

    it('getTemplate with version passes version param', async () => {
      mockJsonResponse({ template_id: '0190c000-0000-7000-0000-000000000001', value: 'TEST', version: 3 })

      await client.templates.getTemplate('0190c000-0000-7000-0000-000000000001', 3)

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/0190c000-0000-7000-0000-000000000001')
      expect(url).toContain('version=3')
    })

    it('getTemplateByValue fetches by value', async () => {
      mockJsonResponse({ template_id: '0190c000-0000-7000-0000-000000000001', value: 'PATIENT' })

      await client.templates.getTemplateByValue('PATIENT')

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/by-value/PATIENT')
    })

    it('createTemplate sends bulk POST and unwraps', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'created', id: '0190c000-0000-7000-0000-000000000001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      const result = await client.templates.createTemplate({
        value: 'PATIENT',
        label: 'Patient',
        fields: [],
      } as any)

      expect(result.status).toBe('created')
      expect(result.id).toBe('0190c000-0000-7000-0000-000000000001')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ value: 'PATIENT', label: 'Patient', fields: [] }])
    })

    it('createTemplates sends bulk POST with multiple items', async () => {
      mockJsonResponse({
        results: [
          { index: 0, status: 'created', id: '0190c000-0000-7000-0000-000000000001' },
          { index: 1, status: 'created', id: 'TPL-2' },
        ],
        total: 2,
        succeeded: 2,
        failed: 0,
      })

      const result = await client.templates.createTemplates([
        { value: 'PATIENT', label: 'Patient', fields: [] } as any,
        { value: 'VISIT', label: 'Visit', fields: [] } as any,
      ])

      expect(result.succeeded).toBe(2)
      expect(result.results).toHaveLength(2)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toHaveLength(2)
      expect(body[0].value).toBe('PATIENT')
      expect(body[1].value).toBe('VISIT')
    })

    it('createTemplate throws WipBulkItemError on error result', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'error', error: 'Invalid fields' }],
        total: 1,
        succeeded: 0,
        failed: 1,
      })

      await expect(
        client.templates.createTemplate({ value: 'BAD', label: 'Bad', fields: [] } as any),
      ).rejects.toThrow(WipBulkItemError)
    })

    it('updateTemplate sends PUT with template_id in body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'updated', id: '0190c000-0000-7000-0000-000000000001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.templates.updateTemplate('0190c000-0000-7000-0000-000000000001', { label: 'Updated Patient' } as any)

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('PUT')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ label: 'Updated Patient', template_id: '0190c000-0000-7000-0000-000000000001' }])
    })

    it('deleteTemplate sends DELETE with body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'deleted', id: '0190c000-0000-7000-0000-000000000001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.templates.deleteTemplate('0190c000-0000-7000-0000-000000000001', { version: 2, force: true })

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ id: '0190c000-0000-7000-0000-000000000001', version: 2, force: true, updated_by: undefined }])
    })

    it('validateTemplate sends POST', async () => {
      mockJsonResponse({ valid: true, errors: [] })

      const result = await client.templates.validateTemplate('0190c000-0000-7000-0000-000000000001', {})

      expect(result.valid).toBe(true)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/0190c000-0000-7000-0000-000000000001/validate')
      expect(options.method).toBe('POST')
    })

    it('getChildren fetches child templates', async () => {
      mockJsonResponse({ items: [{ template_id: 'TPL-2' }], total: 1, page: 1, page_size: 50, pages: 1 })

      const result = await client.templates.getChildren('0190c000-0000-7000-0000-000000000001')

      expect(result.items).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/0190c000-0000-7000-0000-000000000001/children')
    })

    it('activateTemplate sends POST with options', async () => {
      mockJsonResponse({ activated: ['0190c000-0000-7000-0000-000000000001', 'TPL-2'] })

      const result = await client.templates.activateTemplate('0190c000-0000-7000-0000-000000000001', { dry_run: true })

      expect(result.activated).toEqual(['0190c000-0000-7000-0000-000000000001', 'TPL-2'])
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/0190c000-0000-7000-0000-000000000001/activate')
      expect(url).toContain('dry_run=true')
      expect(options.method).toBe('POST')
    })

    it('getTemplateVersions fetches all versions by value', async () => {
      mockJsonResponse({ items: [{ version: 1 }, { version: 2 }], total: 2, page: 1, page_size: 50, pages: 1 })

      const result = await client.templates.getTemplateVersions('PATIENT')

      expect(result.items).toHaveLength(2)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/by-value/PATIENT/versions')
    })
  })

  describe('DocumentStoreService', () => {
    it('listDocuments sends GET with pagination params', async () => {
      mockJsonResponse({ items: [], total: 0, page: 1, page_size: 50, pages: 0 })

      const result = await client.documents.listDocuments({ page: 1, status: 'active', template_id: '0190c000-0000-7000-0000-000000000001' } as any)

      expect(result.items).toEqual([])
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents')
      expect(url).toContain('page=1')
      expect(url).toContain('status=active')
      expect(url).toContain('template_id=0190c000-0000-7000-0000-000000000001')
    })

    it('getDocument fetches by ID', async () => {
      mockJsonResponse({ document_id: 'D-001', data: { name: 'Test' }, version: 1 })

      const result = await client.documents.getDocument('D-001')

      expect(result.document_id).toBe('D-001')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/D-001')
    })

    it('getDocument with version passes version param', async () => {
      mockJsonResponse({ document_id: 'D-001', version: 2 })

      await client.documents.getDocument('D-001', 2)

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/D-001')
      expect(url).toContain('version=2')
    })

    it('createDocument sends bulk POST', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'created', document_id: 'D-001', is_new: true }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      const result = await client.documents.createDocument({
        template_id: '0190c000-0000-7000-0000-000000000001',
        data: { name: 'Test' },
      })

      expect(result.document_id).toBe('D-001')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ template_id: '0190c000-0000-7000-0000-000000000001', data: { name: 'Test' } }])
    })

    it('createDocument throws WipBulkItemError on error result', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'error', error: 'Validation failed' }],
        total: 1,
        succeeded: 0,
        failed: 1,
      })

      await expect(
        client.documents.createDocument({ template_id: '0190c000-0000-7000-0000-000000000001', data: {} }),
      ).rejects.toThrow(WipBulkItemError)
    })

    it('createDocuments sends bulk POST with multiple items', async () => {
      mockJsonResponse({
        results: [
          { index: 0, status: 'created', document_id: 'D-001' },
          { index: 1, status: 'created', document_id: 'D-002' },
        ],
        total: 2,
        succeeded: 2,
        failed: 0,
      })

      const result = await client.documents.createDocuments([
        { template_id: '0190c000-0000-7000-0000-000000000001', data: { name: 'A' } },
        { template_id: '0190c000-0000-7000-0000-000000000001', data: { name: 'B' } },
      ])

      expect(result.succeeded).toBe(2)
      expect(result.results).toHaveLength(2)

      const [, options] = fetchMock.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body).toHaveLength(2)
    })

    it('updateDocument sends bulk PATCH and unwraps single result', async () => {
      mockJsonResponse({
        results: [{
          index: 0,
          status: 'updated',
          document_id: 'D-001',
          identity_hash: 'abc',
          version: 4,
          is_new: false,
        }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      const result = await client.documents.updateDocument('D-001', { score: 92 })

      expect(result.status).toBe('updated')
      expect(result.version).toBe(4)

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents')
      expect(options.method).toBe('PATCH')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ document_id: 'D-001', patch: { score: 92 } }])
    })

    it('updateDocument forwards ifMatch as if_match', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'updated', document_id: 'D-001', version: 5 }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.documents.updateDocument('D-001', { name: 'Jane' }, { ifMatch: 4 })

      const [, options] = fetchMock.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ document_id: 'D-001', patch: { name: 'Jane' }, if_match: 4 }])
    })

    it('updateDocument throws WipBulkItemError carrying error_code', async () => {
      mockJsonResponse({
        results: [{
          index: 0,
          status: 'error',
          document_id: 'D-001',
          error: 'Cannot patch identity field',
          error_code: 'identity_field_change',
        }],
        total: 1,
        succeeded: 0,
        failed: 1,
      })

      try {
        await client.documents.updateDocument('D-001', { national_id: 'X' })
        throw new Error('expected throw')
      } catch (err) {
        expect(err).toBeInstanceOf(WipBulkItemError)
        expect((err as WipBulkItemError).errorCode).toBe('identity_field_change')
      }
    })

    it('updateDocuments sends bulk PATCH with multiple items', async () => {
      mockJsonResponse({
        results: [
          { index: 0, status: 'updated', document_id: 'D-001', version: 2 },
          { index: 1, status: 'error', document_id: 'D-002', error: 'not found', error_code: 'not_found' },
        ],
        total: 2,
        succeeded: 1,
        failed: 1,
      })

      const result = await client.documents.updateDocuments([
        { document_id: 'D-001', patch: { x: 1 } },
        { document_id: 'D-002', patch: { y: 2 } },
      ])

      expect(result.succeeded).toBe(1)
      expect(result.failed).toBe(1)
      expect(result.results[1].error_code).toBe('not_found')

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('PATCH')
    })

    it('deleteDocument sends DELETE with body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'deleted', id: 'D-001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.documents.deleteDocument('D-001', { updatedBy: 'admin' })

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ id: 'D-001', updated_by: 'admin' }])
    })

    it('getTableView sends GET with params', async () => {
      mockJsonResponse({
        template_id: '0190c000-0000-7000-0000-000000000001',
        columns: [],
        rows: [],
        total_documents: 0,
        total_rows: 0,
        page: 1,
        page_size: 50,
        pages: 0,
      })

      await client.documents.getTableView('0190c000-0000-7000-0000-000000000001', { status: 'active' })

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/table/0190c000-0000-7000-0000-000000000001')
      expect(url).toContain('status=active')
    })

    it('queryDocuments sends POST with query body', async () => {
      mockJsonResponse({ items: [{ document_id: 'D-001' }], total: 1, page: 1, page_size: 50, pages: 1 })

      const result = await client.documents.queryDocuments({
        template_id: '0190c000-0000-7000-0000-000000000001',
        filters: { name: 'Test' },
      } as any)

      expect(result.items).toHaveLength(1)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/query')
      expect(options.method).toBe('POST')
    })

    it('getVersions fetches version history', async () => {
      mockJsonResponse({ versions: [{ version: 1 }, { version: 2 }] })

      const result = await client.documents.getVersions('D-001')

      expect(result.versions).toHaveLength(2)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/D-001/versions')
    })

    it('getVersion fetches specific version', async () => {
      mockJsonResponse({ document_id: 'D-001', version: 3 })

      const result = await client.documents.getVersion('D-001', 3)

      expect(result.version).toBe(3)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/D-001/versions/3')
    })

    it('getLatestDocument fetches latest version', async () => {
      mockJsonResponse({ document_id: 'D-001', version: 5 })

      const result = await client.documents.getLatestDocument('D-001')

      expect(result.version).toBe(5)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/D-001/latest')
    })

    it('getDocumentByIdentity fetches by identity hash', async () => {
      mockJsonResponse({ document_id: 'D-001', data: { email: 'a@b.com' } })

      await client.documents.getDocumentByIdentity('abc123hash')

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/by-identity/abc123hash')
    })

    it('archiveDocument sends POST', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'archived', id: 'D-001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      const result = await client.documents.archiveDocument('D-001', 'admin')

      expect(result.status).toBe('archived')
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents/archive')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ id: 'D-001', archived_by: 'admin' }])
    })

    it('validateDocument sends POST', async () => {
      mockJsonResponse({ valid: true, errors: [] })

      const result = await client.documents.validateDocument({
        template_id: '0190c000-0000-7000-0000-000000000001',
        data: { name: 'Test' },
      } as any)

      expect(result.valid).toBe(true)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/validation/validate')
      expect(options.method).toBe('POST')
    })

    // ---- Backup / Restore (CASE-23 Phase 3 STEP 7) ----

    function backupSnapshot(overrides: Record<string, unknown> = {}) {
      return {
        job_id: 'bkp-abc123',
        kind: 'backup',
        namespace: 'aa',
        status: 'pending',
        phase: null,
        percent: null,
        message: null,
        error: null,
        created_at: '2026-04-08T18:30:00Z',
        started_at: null,
        completed_at: null,
        archive_size: null,
        options: {},
        created_by: 'apikey:legacy',
        ...overrides,
      }
    }

    it('startBackup POSTs the request body to the namespace endpoint', async () => {
      mockJsonResponse(backupSnapshot())

      const result = await client.documents.startBackup('aa', {
        include_files: false,
        latest_only: true,
      })

      expect(result.job_id).toBe('bkp-abc123')
      expect(result.status).toBe('pending')
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/backup/namespaces/aa/backup')
      expect(options.method).toBe('POST')
      expect(JSON.parse(options.body)).toEqual({
        include_files: false,
        latest_only: true,
      })
    })

    it('startBackup defaults to an empty body', async () => {
      mockJsonResponse(backupSnapshot())

      await client.documents.startBackup('aa')

      const [, options] = fetchMock.mock.calls[0]
      expect(JSON.parse(options.body)).toEqual({})
    })

    it('startRestore uploads multipart form with the archive and options', async () => {
      mockJsonResponse(
        backupSnapshot({
          job_id: 'rst-xyz789',
          kind: 'restore',
          namespace: 'aa-restored',
        }),
      )

      const archive = new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], {
        type: 'application/zip',
      })
      const result = await client.documents.startRestore(
        'aa-restored',
        archive,
        {
          mode: 'fresh',
          target_namespace: 'aa-restored',
          batch_size: 100,
          register_synonyms: true,
        },
        'aa-backup.zip',
      )

      expect(result.job_id).toBe('rst-xyz789')
      expect(result.kind).toBe('restore')
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/backup/namespaces/aa-restored/restore')
      expect(options.method).toBe('POST')
      expect(options.body).toBeInstanceOf(FormData)
      const fd = options.body as FormData
      expect(fd.get('mode')).toBe('fresh')
      expect(fd.get('target_namespace')).toBe('aa-restored')
      expect(fd.get('batch_size')).toBe('100')
      expect(fd.get('register_synonyms')).toBe('true')
      // Archive part is present and named.
      const archivePart = fd.get('archive')
      expect(archivePart).toBeInstanceOf(Blob)
      expect((archivePart as File).name).toBe('aa-backup.zip')
    })

    it('startRestore omits unset option fields', async () => {
      mockJsonResponse(backupSnapshot({ kind: 'restore' }))

      const archive = new Blob([new Uint8Array([0])], { type: 'application/zip' })
      await client.documents.startRestore('aa', archive)

      const [, options] = fetchMock.mock.calls[0]
      const fd = options.body as FormData
      expect(fd.get('mode')).toBeNull()
      expect(fd.get('target_namespace')).toBeNull()
      expect(fd.get('batch_size')).toBeNull()
      expect(fd.get('archive')).toBeInstanceOf(Blob)
    })

    it('getBackupJob fetches by job_id', async () => {
      mockJsonResponse(backupSnapshot({ status: 'complete', percent: 100, archive_size: 90840 }))

      const result = await client.documents.getBackupJob('bkp-abc123')

      expect(result.status).toBe('complete')
      expect(result.archive_size).toBe(90840)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/backup/jobs/bkp-abc123')
    })

    it('listBackupJobs sends filter params', async () => {
      mockJsonResponse([backupSnapshot()])

      const result = await client.documents.listBackupJobs({
        namespace: 'aa',
        status: 'complete',
        limit: 10,
      })

      expect(result).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/backup/jobs')
      expect(url).toContain('namespace=aa')
      expect(url).toContain('status=complete')
      expect(url).toContain('limit=10')
    })

    it('downloadBackupArchive returns a Blob', async () => {
      const blob = new Blob([new Uint8Array([0x50, 0x4b])], { type: 'application/zip' })
      fetchMock.mockResolvedValue(
        new Response(blob, {
          status: 200,
          headers: { 'Content-Type': 'application/zip' },
        }),
      )

      const result = await client.documents.downloadBackupArchive('bkp-abc123')

      expect(result).toBeInstanceOf(Blob)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/backup/jobs/bkp-abc123/download')
    })

    it('deleteBackupJob sends DELETE', async () => {
      fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

      await client.documents.deleteBackupJob('bkp-abc123')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/backup/jobs/bkp-abc123')
      expect(options.method).toBe('DELETE')
    })

    it('streamBackupJobEvents yields parsed progress messages and stops on done', async () => {
      // Three SSE messages: a comment keep-alive, a progress event, then a
      // terminal complete event. The reader should yield exactly the two
      // progress payloads.
      const sse =
        ': connected\n\n' +
        'event: progress\n' +
        'data: {"job_id":"bkp-1","status":"running","phase":"phase_documents","percent":42,"message":"...","current":null,"total":null,"details":null}\n\n' +
        'event: progress\n' +
        'data: {"job_id":"bkp-1","status":"complete","phase":"complete","percent":100,"message":"done","current":null,"total":null,"details":null}\n\n'
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(sse))
          controller.close()
        },
      })
      fetchMock.mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
      )

      const events: BackupProgressMessageType[] = []
      for await (const evt of client.documents.streamBackupJobEvents('bkp-1')) {
        events.push(evt)
      }

      expect(events).toHaveLength(2)
      expect(events[0].percent).toBe(42)
      expect(events[0].phase).toBe('phase_documents')
      expect(events[1].status).toBe('complete')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/backup/jobs/bkp-1/events')
    })

    it('streamBackupJobEvents skips malformed payloads without aborting', async () => {
      const sse =
        'event: progress\ndata: not-json\n\n' +
        'event: progress\ndata: {"job_id":"bkp-2","status":"running","phase":"x","percent":10,"message":null,"current":null,"total":null,"details":null}\n\n'
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(sse))
          controller.close()
        },
      })
      fetchMock.mockResolvedValue(
        new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
      )

      const events: BackupProgressMessageType[] = []
      for await (const evt of client.documents.streamBackupJobEvents('bkp-2')) {
        events.push(evt)
      }

      expect(events).toHaveLength(1)
      expect(events[0].percent).toBe(10)
    })
  })

  describe('RegistryService', () => {
    it('listNamespaces sends GET', async () => {
      mockJsonResponse([{ prefix: 'wip-terms', status: 'active' }])

      const result = await client.registry.listNamespaces()

      expect(result).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces')
    })

    it('listNamespaces passes include_archived param', async () => {
      mockJsonResponse([])

      await client.registry.listNamespaces(true)

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('include_archived=true')
    })

    it('getNamespace fetches by prefix', async () => {
      mockJsonResponse({ prefix: 'wip-terms', status: 'active', description: 'Terms' })

      const result = await client.registry.getNamespace('wip-terms')

      expect(result.prefix).toBe('wip-terms')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces/wip-terms')
    })

    it('getNamespaceStats fetches stats', async () => {
      mockJsonResponse({ total_entries: 100, active_entries: 95 })

      const result = await client.registry.getNamespaceStats('wip-terms')

      expect(result.total_entries).toBe(100)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces/wip-terms/stats')
    })

    it('createNamespace sends POST', async () => {
      mockJsonResponse({ prefix: 'custom-ns', status: 'active' })

      const result = await client.registry.createNamespace({
        prefix: 'custom-ns',
        description: 'Custom namespace',
      } as any)

      expect(result.prefix).toBe('custom-ns')
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces')
      expect(options.method).toBe('POST')
    })

    it('updateNamespace sends PUT', async () => {
      mockJsonResponse({ prefix: 'wip-terms', description: 'Updated' })

      await client.registry.updateNamespace('wip-terms', { description: 'Updated' })

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces/wip-terms')
      expect(options.method).toBe('PUT')
    })

    it('lookupEntry unwraps results array', async () => {
      mockJsonResponse({
        results: [{ input_index: 0, status: 'found', entry_id: 'E-001' }],
      })

      const result = await client.registry.lookupEntry('E-001')
      expect(result.entry_id).toBe('E-001')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/entries/lookup/by-id')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ entry_id: 'E-001' }])
    })

    it('listEntries sends GET with params', async () => {
      mockJsonResponse({ items: [], total: 0, page: 1, page_size: 50, pages: 0 })

      await client.registry.listEntries({ page: 2, namespace: 'wip-terms' } as any)

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/entries')
      expect(url).toContain('page=2')
      expect(url).toContain('namespace=wip-terms')
    })

    it('getEntry fetches by entry ID', async () => {
      mockJsonResponse({ entry_id: 'E-001', namespace: 'wip-terms', status: 'active' })

      const result = await client.registry.getEntry('E-001')

      expect(result.entry_id).toBe('E-001')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/entries/E-001')
    })

    it('searchEntries sends POST and unwraps nested results', async () => {
      mockJsonResponse({
        results: [{ results: [{ entry_id: 'E-001' }, { entry_id: 'E-002' }] }],
      })

      const result = await client.registry.searchEntries('test', {
        namespaces: ['wip-terms'],
        entityTypes: ['term'],
      })

      expect(result).toHaveLength(2)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/entries/search/by-term')
      expect(options.method).toBe('POST')
    })

    it('unifiedSearch sends GET with params', async () => {
      mockJsonResponse({ items: [], total: 0 })

      await client.registry.unifiedSearch({ q: 'test', limit: 10 } as any)

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/entries/search')
      expect(url).toContain('q=test')
      expect(url).toContain('limit=10')
    })

    it('addSynonym sends POST and unwraps result', async () => {
      mockJsonResponse({
        results: [{ status: 'added', registry_id: 'E-001' }],
      })

      const result = await client.registry.addSynonym({
        entry_id: 'E-001',
        composite_key: { namespace: 'wip-terms', value: 'alias' },
      } as any)

      expect(result.status).toBe('added')
      expect(result.registry_id).toBe('E-001')
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/synonyms/add')
      expect(options.method).toBe('POST')
    })

    it('removeSynonym sends POST and unwraps result', async () => {
      mockJsonResponse({
        results: [{ status: 'removed', registry_id: 'E-001' }],
      })

      const result = await client.registry.removeSynonym({
        entry_id: 'E-001',
        composite_key: { namespace: 'wip-terms', value: 'alias' },
      } as any)

      expect(result.status).toBe('removed')
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/synonyms/remove')
      expect(options.method).toBe('POST')
    })

    it('mergeEntries sends POST and unwraps result', async () => {
      mockJsonResponse({
        results: [{ status: 'merged', preferred_id: 'E-001', deprecated_id: 'E-002' }],
      })

      const result = await client.registry.mergeEntries({
        preferred_id: 'E-001',
        deprecated_id: 'E-002',
      } as any)

      expect(result.status).toBe('merged')
      expect(result.preferred_id).toBe('E-001')
      expect(result.deprecated_id).toBe('E-002')
    })

    it('deactivateEntry sends DELETE and unwraps result', async () => {
      mockJsonResponse({
        results: [{ status: 'deactivated' }],
      })

      const result = await client.registry.deactivateEntry('E-001', 'admin')

      expect(result.status).toBe('deactivated')
      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ entry_id: 'E-001', updated_by: 'admin' }])
    })

    it('archiveNamespace sends POST', async () => {
      mockJsonResponse({ prefix: 'wip-terms', status: 'archived' })

      const result = await client.registry.archiveNamespace('wip-terms', 'admin')

      expect(result.status).toBe('archived')
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces/wip-terms/archive')
      expect(url).toContain('archived_by=admin')
      expect(options.method).toBe('POST')
    })

    it('initializeWipNamespace sends POST', async () => {
      mockJsonResponse({ prefix: 'wip', status: 'active' })

      await client.registry.initializeWipNamespace()

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces/initialize-wip')
      expect(options.method).toBe('POST')
    })
  })

  describe('FileStoreService', () => {
    it('listFiles sends GET with params', async () => {
      mockJsonResponse({ items: [], total: 0, page: 1, page_size: 50, pages: 0 })

      const result = await client.files.listFiles({ page: 1, status: 'active' } as any)

      expect(result.items).toEqual([])
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files')
      expect(url).toContain('page=1')
      expect(url).toContain('status=active')
    })

    it('getFile fetches by ID', async () => {
      mockJsonResponse({ file_id: 'F-001', filename: 'test.pdf', status: 'active' })

      const result = await client.files.getFile('F-001')

      expect(result.file_id).toBe('F-001')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files/F-001')
    })

    it('uploadFile sends POST with FormData', async () => {
      mockJsonResponse({ file_id: 'F-001', filename: 'test.pdf' })

      const blob = new Blob(['test content'], { type: 'application/pdf' })
      await client.files.uploadFile(blob, 'test.pdf', {
        description: 'A test file',
        tags: ['test', 'pdf'],
        category: 'reports',
      })

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files')
      expect(options.method).toBe('POST')
      expect(options.body).toBeInstanceOf(FormData)
      const formData = options.body as FormData
      expect(formData.get('file')).toBeInstanceOf(Blob)
      expect(formData.get('description')).toBe('A test file')
      expect(formData.get('tags')).toBe('test,pdf')
      expect(formData.get('category')).toBe('reports')
    })

    it('getDownloadUrl fetches presigned URL', async () => {
      mockJsonResponse({ url: 'https://minio.local/files/F-001', expires_in: 3600 })

      const result = await client.files.getDownloadUrl('F-001', 3600)

      expect(result.url).toContain('minio.local')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files/F-001/download')
      expect(url).toContain('expires_in=3600')
    })

    it('updateMetadata sends PATCH with file_id in body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'updated', id: 'F-001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.files.updateMetadata('F-001', { description: 'Updated desc' } as any)

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('PATCH')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ description: 'Updated desc', file_id: 'F-001' }])
    })

    it('deleteFile sends DELETE with body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'deleted', id: 'F-001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.files.deleteFile('F-001')

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ id: 'F-001' }])
    })

    it('deleteFiles sends bulk DELETE', async () => {
      mockJsonResponse({
        results: [
          { index: 0, status: 'deleted', id: 'F-001' },
          { index: 1, status: 'deleted', id: 'F-002' },
        ],
        total: 2,
        succeeded: 2,
        failed: 0,
      })

      const result = await client.files.deleteFiles(['F-001', 'F-002'])

      expect(result.succeeded).toBe(2)
      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ id: 'F-001' }, { id: 'F-002' }])
    })

    it('hardDeleteFile sends DELETE to /hard endpoint', async () => {
      mockJsonResponse(undefined)

      await client.files.hardDeleteFile('F-001')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files/F-001/hard')
      expect(options.method).toBe('DELETE')
    })

    it('listOrphans sends GET with params', async () => {
      mockJsonResponse([{ file_id: 'F-003', filename: 'orphan.txt' }])

      const result = await client.files.listOrphans({ older_than_hours: 24, limit: 10 })

      expect(result).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files/orphans/list')
      expect(url).toContain('older_than_hours=24')
      expect(url).toContain('limit=10')
    })

    it('findByChecksum sends GET', async () => {
      mockJsonResponse([{ file_id: 'F-001' }])

      const result = await client.files.findByChecksum('sha256abc')

      expect(result).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files/by-checksum/sha256abc')
    })

    it('checkIntegrity sends GET', async () => {
      mockJsonResponse({ total_files: 50, issues: [] })

      const result = await client.files.checkIntegrity()

      expect(result.total_files).toBe(50)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files/health/integrity')
    })

    it('getFileDocuments sends GET with pagination', async () => {
      mockJsonResponse({ items: [], total: 0, page: 1, page_size: 10, pages: 0 })

      const result = await client.files.getFileDocuments('F-001', 2, 20)

      expect(result.total).toBe(0)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/files/F-001/documents')
      expect(url).toContain('page=2')
      expect(url).toContain('page_size=20')
    })
  })

  describe('ReportingSyncService', () => {
    it('getIntegrityCheck sends GET with params', async () => {
      mockJsonResponse({ status: 'ok', mismatches: [] })

      const result = await client.reporting.getIntegrityCheck({
        template_status: 'active',
        document_status: 'active',
        check_term_refs: true,
      })

      expect(result.status).toBe('ok')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/health/integrity')
      expect(url).toContain('template_status=active')
      expect(url).toContain('document_status=active')
      expect(url).toContain('check_term_refs=true')
    })

    it('search sends POST with query', async () => {
      mockJsonResponse({ results: [{ id: 'D-001', type: 'document' }], total: 1 })

      const result = await client.reporting.search({
        query: 'patient',
        types: ['document'],
        limit: 10,
      })

      expect(result.results).toHaveLength(1)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/search')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body.query).toBe('patient')
      expect(body.types).toEqual(['document'])
      expect(body.limit).toBe(10)
    })

    it('getRecentActivity sends GET', async () => {
      mockJsonResponse({ activities: [{ type: 'document', action: 'created' }] })

      const result = await client.reporting.getRecentActivity({ types: 'document', limit: 5 })

      expect(result.activities).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/activity/recent')
      expect(url).toContain('types=document')
      expect(url).toContain('limit=5')
    })

    it('getTermDocuments sends GET', async () => {
      mockJsonResponse({ documents: [{ document_id: 'D-001' }], total: 1 })

      const result = await client.reporting.getTermDocuments('0190b000-0000-7000-0000-000000000001', 50)

      expect(result.documents).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/references/term/0190b000-0000-7000-0000-000000000001/documents')
      expect(url).toContain('limit=50')
    })

    it('getEntityReferences sends GET', async () => {
      mockJsonResponse({ references: [{ type: 'term', id: '0190b000-0000-7000-0000-000000000001' }] })

      const result = await client.reporting.getEntityReferences('document', 'D-001')

      expect(result.references).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/entity/document/D-001/references')
    })

    it('getReferencedBy sends GET with limit', async () => {
      mockJsonResponse({ referenced_by: [{ type: 'document', id: 'D-001' }] })

      const result = await client.reporting.getReferencedBy('term', '0190b000-0000-7000-0000-000000000001', 50)

      expect(result.referenced_by).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/entity/term/0190b000-0000-7000-0000-000000000001/referenced-by')
      expect(url).toContain('limit=50')
    })

    it('healthCheck returns true on success', async () => {
      mockJsonResponse({ status: 'ok' })

      const result = await client.reporting.healthCheck()

      expect(result).toBe(true)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/health')
    })

    it('healthCheck returns false on failure', async () => {
      fetchMock.mockRejectedValue(new Error('Connection refused'))

      const result = await client.reporting.healthCheck()

      expect(result).toBe(false)
    })

    it('getSyncStatus sends GET', async () => {
      mockJsonResponse({
        running: true,
        connected_to_nats: true,
        connected_to_postgres: true,
        last_event_processed: '2026-03-29T12:00:00Z',
        events_processed: 42,
        events_failed: 0,
        tables_managed: 5,
      })

      const result = await client.reporting.getSyncStatus()

      expect(result.running).toBe(true)
      expect(result.events_processed).toBe(42)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/status')
    })

    it('runQuery sends POST with SQL and params', async () => {
      mockJsonResponse({
        columns: ['id', 'name'],
        rows: [['D-001', 'Test']],
        row_count: 1,
        truncated: false,
      })

      const result = await client.reporting.runQuery(
        'SELECT * FROM dnd_monster WHERE document_id = $1',
        ['D-001'],
        { timeout_seconds: 10, max_rows: 100 },
      )

      expect(result.columns).toEqual(['id', 'name'])
      expect(result.row_count).toBe(1)
      expect(result.truncated).toBe(false)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/query')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body.sql).toBe('SELECT * FROM dnd_monster WHERE document_id = $1')
      expect(body.params).toEqual(['D-001'])
      expect(body.timeout_seconds).toBe(10)
      expect(body.max_rows).toBe(100)
    })

    it('runQuery sends POST with defaults when no options', async () => {
      mockJsonResponse({ columns: [], rows: [], row_count: 0, truncated: false })

      await client.reporting.runQuery('SELECT 1')

      const [, options] = fetchMock.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.sql).toBe('SELECT 1')
      expect(body.params).toEqual([])
      expect(body.timeout_seconds).toBeUndefined()
      expect(body.max_rows).toBeUndefined()
    })

    it('listTables sends GET without filter', async () => {
      mockJsonResponse({ tables: [{ table_name: 'dnd_monster', row_count: 100 }] })

      const result = await client.reporting.listTables()

      expect(result.tables).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/tables')
      expect(url).not.toContain('table_name')
    })

    it('listTables sends GET with table name filter', async () => {
      mockJsonResponse({ tables: [{ table_name: 'dnd_monster', row_count: 100 }] })

      await client.reporting.listTables('dnd_monster')

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/tables')
      expect(url).toContain('table_name=dnd_monster')
    })

    it('getTableSchema sends GET with template value', async () => {
      mockJsonResponse({
        template_value: 'DND_MONSTER',
        table_name: 'dnd_monster',
        columns: [{ name: 'document_id', type: 'text', nullable: false }],
        row_count: 100,
      })

      const result = await client.reporting.getTableSchema('DND_MONSTER')

      expect(result.template_value).toBe('DND_MONSTER')
      expect(result.columns).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/schema/DND_MONSTER')
    })

    it('awaitSync resolves when events_processed increases', async () => {
      // First call: getSyncStatus returns events_processed=10
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({
          running: true, connected_to_nats: true, connected_to_postgres: true,
          last_event_processed: null, events_processed: 10, events_failed: 0, tables_managed: 0,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      )
      // Second call (after interval): events_processed=11
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({
          running: true, connected_to_nats: true, connected_to_postgres: true,
          last_event_processed: null, events_processed: 11, events_failed: 0, tables_managed: 0,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      )

      await client.reporting.awaitSync({ timeout: 2000, interval: 50 })

      expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('awaitSync throws on timeout when no events process', async () => {
      // Always return the same events_processed count (fresh Response each call)
      fetchMock.mockImplementation(() => Promise.resolve(
        new Response(JSON.stringify({
          running: true, connected_to_nats: true, connected_to_postgres: true,
          last_event_processed: null, events_processed: 10, events_failed: 0, tables_managed: 0,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      ))

      await expect(
        client.reporting.awaitSync({ timeout: 200, interval: 50 }),
      ).rejects.toThrow('Sync timeout: no new events processed')
    })

    it('awaitSync query mode resolves when row found', async () => {
      // First query: no rows
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({
          columns: ['id'], rows: [], row_count: 0, truncated: false,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      )
      // Second query: row found
      fetchMock.mockResolvedValueOnce(
        new Response(JSON.stringify({
          columns: ['id'], rows: [['D-001']], row_count: 1, truncated: false,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      )

      await client.reporting.awaitSync({
        query: 'SELECT 1 FROM dnd_monster WHERE document_id = $1',
        params: ['D-001'],
        timeout: 2000,
        interval: 50,
      })

      expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('awaitSync query mode throws on timeout when row not found', async () => {
      // Always return empty result (fresh Response each call)
      fetchMock.mockImplementation(() => Promise.resolve(
        new Response(JSON.stringify({
          columns: ['id'], rows: [], row_count: 0, truncated: false,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
      ))

      await expect(
        client.reporting.awaitSync({
          query: 'SELECT 1 FROM missing_table',
          timeout: 200,
          interval: 50,
        }),
      ).rejects.toThrow('Sync timeout: expected data not found')
    })
  })

  describe('Auth header injection', () => {
    it('includes X-API-Key header', async () => {
      mockJsonResponse({})
      await client.defStore.listTerminologies()

      const [, options] = fetchMock.mock.calls[0]
      expect(options.headers['X-API-Key']).toBe('test-key')
    })

    it('setAuth changes auth provider', async () => {
      mockJsonResponse({})
      client.setAuth({ getHeaders: () => ({ Authorization: 'Bearer token123' }) })
      await client.defStore.listTerminologies()

      const [, options] = fetchMock.mock.calls[0]
      expect(options.headers['Authorization']).toBe('Bearer token123')
      expect(options.headers['X-API-Key']).toBeUndefined()
    })
  })
})
