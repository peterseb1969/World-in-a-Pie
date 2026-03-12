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
    it('getTemplate fetches by ID', async () => {
      mockJsonResponse({ template_id: 'TPL-1', value: 'TEST', version: 1 })

      const result = await client.templates.getTemplate('TPL-1')

      expect(result.template_id).toBe('TPL-1')
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/TPL-1')
    })

    it('getTemplateByValue fetches by value', async () => {
      mockJsonResponse({ template_id: 'TPL-1', value: 'PATIENT' })

      await client.templates.getTemplateByValue('PATIENT')

      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/template-store/templates/by-value/PATIENT')
    })
  })

  describe('DocumentStoreService', () => {
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
  })

  describe('RegistryService', () => {
    it('listNamespaces sends GET', async () => {
      mockJsonResponse([{ prefix: 'wip-terms', status: 'active' }])

      const result = await client.registry.listNamespaces()

      expect(result).toHaveLength(1)
      const [url] = fetchMock.mock.calls[0]
      expect(url).toContain('/api/registry/namespaces')
    })

    it('lookupEntry unwraps results array', async () => {
      mockJsonResponse({
        results: [{ input_index: 0, status: 'found', entry_id: 'E-001' }],
      })

      const result = await client.registry.lookupEntry('E-001')
      expect(result.entry_id).toBe('E-001')
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
