import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { WipProvider } from '../src/provider'
import { useTerminologies, useTerminology } from '../src/hooks/use-terminologies'
import { useTerms, useTerm } from '../src/hooks/use-terms'
import { useTemplates, useTemplate } from '../src/hooks/use-templates'
import { useDocuments, useDocument } from '../src/hooks/use-documents'
import { useFiles, useFile } from '../src/hooks/use-files'
import {
  useCreateTerminology,
  useCreateTerm,
  useDeleteTerminology,
  useDeleteTerm,
  useCreateDocument,
  useDeleteDocument,
  useUploadFile,
} from '../src/hooks/use-mutations'
import { wipKeys } from '../src/utils/keys'
import type { WipClient } from '@wip/client'
import type { ReactNode } from 'react'

// ---------------------------------------------------------------------------
// Mock WipClient factory
// ---------------------------------------------------------------------------

function createMockClient() {
  return {
    defStore: {
      listTerminologies: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getTerminology: vi.fn().mockResolvedValue({ entity_id: 't1', value: 'Test' }),
      createTerminology: vi.fn().mockResolvedValue({ entity_id: 't1', status: 'created' }),
      deleteTerminology: vi.fn().mockResolvedValue({ entity_id: 't1', status: 'deleted' }),
      listTerms: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getTerm: vi.fn().mockResolvedValue({ entity_id: 'term1', value: 'Term' }),
      createTerm: vi.fn().mockResolvedValue({ entity_id: 'term1', status: 'created' }),
      deleteTerm: vi.fn().mockResolvedValue({ entity_id: 'term1', status: 'deleted' }),
    },
    templates: {
      listTemplates: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getTemplate: vi.fn().mockResolvedValue({ entity_id: 'tpl1', value: 'Template' }),
      getTemplateByValue: vi.fn().mockResolvedValue({ entity_id: 'tpl1', value: 'Template' }),
      createTemplate: vi.fn().mockResolvedValue({ entity_id: 'tpl1', status: 'created' }),
    },
    documents: {
      listDocuments: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getDocument: vi.fn().mockResolvedValue({ entity_id: 'doc1', data: {} }),
      queryDocuments: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getVersions: vi.fn().mockResolvedValue({ versions: [] }),
      createDocument: vi.fn().mockResolvedValue({ entity_id: 'doc1', status: 'created' }),
      createDocuments: vi.fn().mockResolvedValue({ items: [] }),
      deleteDocument: vi.fn().mockResolvedValue({ entity_id: 'doc1', status: 'deleted' }),
    },
    files: {
      listFiles: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getFile: vi.fn().mockResolvedValue({ entity_id: 'f1', filename: 'test.txt' }),
      getDownloadUrl: vi.fn().mockResolvedValue({ url: 'https://example.com/f1' }),
      uploadFile: vi.fn().mockResolvedValue({ entity_id: 'f1', filename: 'uploaded.txt' }),
    },
    registry: {},
    reporting: {},
    setAuth: vi.fn(),
  } as unknown as WipClient
}

// ---------------------------------------------------------------------------
// Wrapper factory
// ---------------------------------------------------------------------------

function createWrapper(client: WipClient) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return {
    queryClient,
    Wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          <WipProvider client={client}>{children}</WipProvider>
        </QueryClientProvider>
      )
    },
  }
}

// ===========================================================================
// Query hooks
// ===========================================================================

describe('Query hooks', () => {
  let mockClient: ReturnType<typeof createMockClient>

  beforeEach(() => {
    mockClient = createMockClient()
  })

  // ---- Terminologies ----

  describe('useTerminologies', () => {
    it('calls client.defStore.listTerminologies', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerminologies(), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.defStore.listTerminologies).toHaveBeenCalledWith(undefined)
    })

    it('passes params to listTerminologies', async () => {
      const params = { page: 2, page_size: 10 }
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerminologies(params), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.defStore.listTerminologies).toHaveBeenCalledWith(params)
    })
  })

  describe('useTerminology', () => {
    it('calls client.defStore.getTerminology with id', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerminology('t1'), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.defStore.getTerminology).toHaveBeenCalledWith('t1')
    })

    it('is disabled when id is empty string', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerminology(''), { wrapper: Wrapper })

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.defStore.getTerminology).not.toHaveBeenCalled()
    })

    it('is disabled when id is undefined', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(
        () => useTerminology(undefined as unknown as string),
        { wrapper: Wrapper },
      )

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.defStore.getTerminology).not.toHaveBeenCalled()
    })
  })

  // ---- Terms ----

  describe('useTerms', () => {
    it('calls client.defStore.listTerms with terminologyId', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerms('tid1'), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.defStore.listTerms).toHaveBeenCalledWith('tid1', undefined)
    })

    it('passes params to listTerms', async () => {
      const params = { page: 1, search: 'abc' }
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerms('tid1', params), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.defStore.listTerms).toHaveBeenCalledWith('tid1', params)
    })

    it('is disabled when terminologyId is empty', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerms(''), { wrapper: Wrapper })

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.defStore.listTerms).not.toHaveBeenCalled()
    })
  })

  describe('useTerm', () => {
    it('calls client.defStore.getTerm with id', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerm('term1'), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.defStore.getTerm).toHaveBeenCalledWith('term1')
    })

    it('is disabled when id is empty string', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTerm(''), { wrapper: Wrapper })

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.defStore.getTerm).not.toHaveBeenCalled()
    })
  })

  // ---- Templates ----

  describe('useTemplates', () => {
    it('calls client.templates.listTemplates', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTemplates(), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.templates.listTemplates).toHaveBeenCalledWith(undefined)
    })

    it('passes params to listTemplates', async () => {
      const params = { status: 'active', latest_only: true }
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTemplates(params), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.templates.listTemplates).toHaveBeenCalledWith(params)
    })
  })

  describe('useTemplate', () => {
    it('calls client.templates.getTemplate with id', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTemplate('tpl1'), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.templates.getTemplate).toHaveBeenCalledWith('tpl1')
    })

    it('is disabled when id is empty string', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useTemplate(''), { wrapper: Wrapper })

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.templates.getTemplate).not.toHaveBeenCalled()
    })

    it('is disabled when id is undefined', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(
        () => useTemplate(undefined as unknown as string),
        { wrapper: Wrapper },
      )

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.templates.getTemplate).not.toHaveBeenCalled()
    })
  })

  // ---- Documents ----

  describe('useDocuments', () => {
    it('calls client.documents.listDocuments', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useDocuments(), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.documents.listDocuments).toHaveBeenCalledWith(undefined)
    })

    it('passes params to listDocuments', async () => {
      const params = { page: 3 }
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useDocuments(params), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.documents.listDocuments).toHaveBeenCalledWith(params)
    })
  })

  describe('useDocument', () => {
    it('calls client.documents.getDocument with id', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useDocument('doc1'), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.documents.getDocument).toHaveBeenCalledWith('doc1')
    })

    it('is disabled when id is empty string', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useDocument(''), { wrapper: Wrapper })

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.documents.getDocument).not.toHaveBeenCalled()
    })

    it('is disabled when id is undefined', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(
        () => useDocument(undefined as unknown as string),
        { wrapper: Wrapper },
      )

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.documents.getDocument).not.toHaveBeenCalled()
    })
  })

  // ---- Files ----

  describe('useFiles', () => {
    it('calls client.files.listFiles', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useFiles(), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.files.listFiles).toHaveBeenCalledWith(undefined)
    })
  })

  describe('useFile', () => {
    it('calls client.files.getFile with id', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useFile('f1'), { wrapper: Wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))
      expect(mockClient.files.getFile).toHaveBeenCalledWith('f1')
    })

    it('is disabled when id is empty string', () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useFile(''), { wrapper: Wrapper })

      expect(result.current.fetchStatus).toBe('idle')
      expect(mockClient.files.getFile).not.toHaveBeenCalled()
    })
  })
})

// ===========================================================================
// Mutation hooks
// ===========================================================================

describe('Mutation hooks', () => {
  let mockClient: ReturnType<typeof createMockClient>

  beforeEach(() => {
    mockClient = createMockClient()
  })

  describe('useCreateTerminology', () => {
    it('calls client.defStore.createTerminology', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const { result } = renderHook(() => useCreateTerminology(), { wrapper: Wrapper })

      const data = { value: 'NewTerminology' }
      await act(() => result.current.mutateAsync(data as any))

      expect(mockClient.defStore.createTerminology).toHaveBeenCalledWith(data)
    })

    it('invalidates terminologies cache on success', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
      const { result } = renderHook(() => useCreateTerminology(), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync({ value: 'New' } as any))

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.terminologies.all,
      })
    })
  })

  describe('useCreateTerm', () => {
    it('calls client.defStore.createTerm with terminologyId', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useCreateTerm('tid1'), { wrapper: Wrapper })

      const data = { value: 'NewTerm' }
      await act(() => result.current.mutateAsync(data as any))

      expect(mockClient.defStore.createTerm).toHaveBeenCalledWith('tid1', data)
    })

    it('invalidates terms and terminology detail cache on success', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
      const { result } = renderHook(() => useCreateTerm('tid1'), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync({ value: 'New' } as any))

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.terms.all,
      })
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.terminologies.detail('tid1'),
      })
    })
  })

  describe('useDeleteTerminology', () => {
    it('calls client.defStore.deleteTerminology', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useDeleteTerminology(), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync('t1'))

      expect(mockClient.defStore.deleteTerminology).toHaveBeenCalledWith('t1')
    })

    it('invalidates terminologies cache on success', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
      const { result } = renderHook(() => useDeleteTerminology(), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync('t1'))

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.terminologies.all,
      })
    })
  })

  describe('useDeleteTerm', () => {
    it('calls client.defStore.deleteTerm', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useDeleteTerm('tid1'), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync('term1'))

      expect(mockClient.defStore.deleteTerm).toHaveBeenCalledWith('term1')
    })

    it('invalidates terms and terminology detail cache on success', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
      const { result } = renderHook(() => useDeleteTerm('tid1'), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync('term1'))

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.terms.all,
      })
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.terminologies.detail('tid1'),
      })
    })
  })

  describe('useCreateDocument', () => {
    it('calls client.documents.createDocument', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useCreateDocument(), { wrapper: Wrapper })

      const data = { template_id: 'tpl1', data: { name: 'test' } }
      await act(() => result.current.mutateAsync(data as any))

      expect(mockClient.documents.createDocument).toHaveBeenCalledWith(data)
    })

    it('invalidates documents cache on success', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
      const { result } = renderHook(() => useCreateDocument(), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync({ template_id: 'tpl1', data: {} } as any))

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.documents.all,
      })
    })
  })

  describe('useDeleteDocument', () => {
    it('calls client.documents.deleteDocument with id and updatedBy', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useDeleteDocument(), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync({ id: 'doc1', updatedBy: 'user1' }))

      expect(mockClient.documents.deleteDocument).toHaveBeenCalledWith('doc1', { updatedBy: 'user1' })
    })

    it('invalidates documents cache on success', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
      const { result } = renderHook(() => useDeleteDocument(), { wrapper: Wrapper })

      await act(() => result.current.mutateAsync({ id: 'doc1' }))

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.documents.all,
      })
    })
  })

  describe('useUploadFile', () => {
    it('calls client.files.uploadFile with file, filename, and metadata', async () => {
      const { Wrapper } = createWrapper(mockClient)
      const { result } = renderHook(() => useUploadFile(), { wrapper: Wrapper })

      const file = new Blob(['hello'], { type: 'text/plain' })
      const metadata = { document_id: 'doc1' }
      await act(() =>
        result.current.mutateAsync({ file, filename: 'test.txt', metadata: metadata as any }),
      )

      expect(mockClient.files.uploadFile).toHaveBeenCalledWith(file, 'test.txt', metadata)
    })

    it('invalidates files cache on success', async () => {
      const { Wrapper, queryClient } = createWrapper(mockClient)
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
      const { result } = renderHook(() => useUploadFile(), { wrapper: Wrapper })

      const file = new Blob(['data'])
      await act(() => result.current.mutateAsync({ file }))

      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: wipKeys.files.all,
      })
    })
  })
})
