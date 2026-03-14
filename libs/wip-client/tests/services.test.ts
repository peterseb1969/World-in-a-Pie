import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createWipClient } from '../src/client'
import type { WipClient } from '../src/client'
import { WipBulkItemError } from '../src/errors'

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
        results: [{ index: 0, status: 'created', id: 'T-001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      const result = await client.defStore.createTerminology({
        value: 'GENDER',
        label: 'Gender',
      })

      expect(result.status).toBe('created')
      expect(result.id).toBe('T-001')

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
        results: [{ index: 0, status: 'deleted', id: 'T-001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.defStore.deleteTerminology('T-001')

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      expect(JSON.parse(options.body)).toEqual([{ id: 'T-001' }])
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
      mockJsonResponse({ template_id: 'TPL-1', value: 'TEST', version: 1 })

      const result = await client.templates.getTemplate('TPL-1')

      expect(result.template_id).toBe('TPL-1')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/TPL-1')
    })

    it('getTemplate with version passes version param', async () => {
      mockJsonResponse({ template_id: 'TPL-1', value: 'TEST', version: 3 })

      await client.templates.getTemplate('TPL-1', 3)

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/TPL-1')
      expect(url).toContain('version=3')
    })

    it('getTemplateByValue fetches by value', async () => {
      mockJsonResponse({ template_id: 'TPL-1', value: 'PATIENT' })

      await client.templates.getTemplateByValue('PATIENT')

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/by-value/PATIENT')
    })

    it('createTemplate sends bulk POST and unwraps', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'created', id: 'TPL-1' }],
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
      expect(result.id).toBe('TPL-1')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ value: 'PATIENT', label: 'Patient', fields: [] }])
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
        results: [{ index: 0, status: 'updated', id: 'TPL-1' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.templates.updateTemplate('TPL-1', { label: 'Updated Patient' } as any)

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('PUT')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ label: 'Updated Patient', template_id: 'TPL-1' }])
    })

    it('deleteTemplate sends DELETE with body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'deleted', id: 'TPL-1' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.templates.deleteTemplate('TPL-1', { version: 2, force: true })

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ id: 'TPL-1', version: 2, force: true, updated_by: undefined }])
    })

    it('validateTemplate sends POST', async () => {
      mockJsonResponse({ valid: true, errors: [] })

      const result = await client.templates.validateTemplate('TPL-1', {})

      expect(result.valid).toBe(true)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/TPL-1/validate')
      expect(options.method).toBe('POST')
    })

    it('getChildren fetches child templates', async () => {
      mockJsonResponse({ items: [{ template_id: 'TPL-2' }], total: 1, page: 1, page_size: 50, pages: 1 })

      const result = await client.templates.getChildren('TPL-1')

      expect(result.items).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/TPL-1/children')
    })

    it('activateTemplate sends POST with options', async () => {
      mockJsonResponse({ activated: ['TPL-1', 'TPL-2'] })

      const result = await client.templates.activateTemplate('TPL-1', { dry_run: true })

      expect(result.activated).toEqual(['TPL-1', 'TPL-2'])
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/TPL-1/activate')
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

      const result = await client.documents.listDocuments({ page: 1, status: 'active', template_id: 'TPL-1' } as any)

      expect(result.items).toEqual([])
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents')
      expect(url).toContain('page=1')
      expect(url).toContain('status=active')
      expect(url).toContain('template_id=TPL-1')
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
        template_id: 'TPL-1',
        data: { name: 'Test' },
      })

      expect(result.document_id).toBe('D-001')

      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/documents')
      expect(options.method).toBe('POST')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ template_id: 'TPL-1', data: { name: 'Test' } }])
    })

    it('createDocument throws WipBulkItemError on error result', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'error', error: 'Validation failed' }],
        total: 1,
        succeeded: 0,
        failed: 1,
      })

      await expect(
        client.documents.createDocument({ template_id: 'TPL-1', data: {} }),
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
        { template_id: 'TPL-1', data: { name: 'A' } },
        { template_id: 'TPL-1', data: { name: 'B' } },
      ])

      expect(result.succeeded).toBe(2)
      expect(result.results).toHaveLength(2)

      const [, options] = fetchMock.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body).toHaveLength(2)
    })

    it('deleteDocument sends DELETE with body', async () => {
      mockJsonResponse({
        results: [{ index: 0, status: 'deleted', id: 'D-001' }],
        total: 1,
        succeeded: 1,
        failed: 0,
      })

      await client.documents.deleteDocument('D-001', 'admin')

      const [, options] = fetchMock.mock.calls[0]
      expect(options.method).toBe('DELETE')
      const body = JSON.parse(options.body)
      expect(body).toEqual([{ id: 'D-001', updated_by: 'admin' }])
    })

    it('getTableView sends GET with params', async () => {
      mockJsonResponse({
        template_id: 'TPL-1',
        columns: [],
        rows: [],
        total_documents: 0,
        total_rows: 0,
        page: 1,
        page_size: 50,
        pages: 0,
      })

      await client.documents.getTableView('TPL-1', { status: 'active' })

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/table/TPL-1')
      expect(url).toContain('status=active')
    })

    it('queryDocuments sends POST with query body', async () => {
      mockJsonResponse({ items: [{ document_id: 'D-001' }], total: 1, page: 1, page_size: 50, pages: 1 })

      const result = await client.documents.queryDocuments({
        template_id: 'TPL-1',
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
        template_id: 'TPL-1',
        data: { name: 'Test' },
      } as any)

      expect(result.valid).toBe(true)
      const [url, options] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/document-store/validation/validate')
      expect(options.method).toBe('POST')
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

      const result = await client.reporting.getTermDocuments('T-001', 50)

      expect(result.documents).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/references/term/T-001/documents')
      expect(url).toContain('limit=50')
    })

    it('getEntityReferences sends GET', async () => {
      mockJsonResponse({ references: [{ type: 'term', id: 'T-001' }] })

      const result = await client.reporting.getEntityReferences('document', 'D-001')

      expect(result.references).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/entity/document/D-001/references')
    })

    it('getReferencedBy sends GET with limit', async () => {
      mockJsonResponse({ referenced_by: [{ type: 'document', id: 'D-001' }] })

      const result = await client.reporting.getReferencedBy('term', 'T-001', 50)

      expect(result.referenced_by).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/reporting-sync/entity/term/T-001/referenced-by')
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
