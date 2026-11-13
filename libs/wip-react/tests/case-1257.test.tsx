/**
 * @wip/react fixes from a code review of the mutation, document and form
 * hooks: a document query the server takes runs whatever it filters on, a
 * form schema can be the version a document is pinned to, document writes
 * refresh the file views whose reference counts they move, entity creates
 * refresh Registry searches, and a bulk import's progress timer is cancelled
 * when a new run starts or the hook unmounts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { WipClient } from '@wip/client'

vi.mock('@wip/client', async () => {
  const actual = await vi.importActual<typeof import('@wip/client')>('@wip/client')
  return {
    ...actual,
    templateToFormSchema: vi.fn(() => []),
    bulkImport: vi.fn(async (items: unknown[], writeFn: (b: unknown[]) => Promise<unknown>, options?: { onProgress?: (p: unknown) => void }) => {
      const response = await writeFn(items)
      options?.onProgress?.({ total: items.length, processed: items.length, succeeded: items.length, failed: 0 })
      return response
    }),
  }
})

import { WipProvider } from '../src/provider'
import { wipKeys } from '../src/utils/keys'
import { useQueryDocuments } from '../src/hooks/use-documents'
import { useFormSchema } from '../src/hooks/use-form-schema'
import { useCreateDocument, useUpdateDocument, useCreateTemplate } from '../src/hooks/use-mutations'
import { useBulkImport } from '../src/hooks/use-bulk-import'

function createMockClient() {
  return {
    documents: {
      queryDocuments: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      createDocument: vi.fn().mockResolvedValue({ index: 0, status: 'created', document_id: 'd1' }),
      updateDocument: vi.fn().mockResolvedValue({ index: 0, status: 'updated', document_id: 'd1' }),
    },
    templates: {
      getTemplateByValue: vi.fn().mockResolvedValue({ value: 'T', version: 5, fields: [] }),
      getTemplateByValueAndVersion: vi.fn().mockResolvedValue({ value: 'T', version: 3, fields: [] }),
      createTemplate: vi.fn().mockResolvedValue({ index: 0, status: 'created' }),
    },
    setAuth: vi.fn(),
  } as unknown as WipClient
}

function createWrapper(client: WipClient) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
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

let client: WipClient
beforeEach(() => {
  client = createMockClient()
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('useQueryDocuments runs any query the server takes', () => {
  it('a status-only query runs', async () => {
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useQueryDocuments({ status: 'active', page_size: 50 }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.documents.queryDocuments).toHaveBeenCalledWith({ status: 'active', page_size: 50 })
  })

  it('a query naming a template it does not have yet waits', () => {
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useQueryDocuments({ template_id: undefined }), { wrapper: Wrapper })
    expect(result.current.fetchStatus).toBe('idle')
    expect(client.documents.queryDocuments).not.toHaveBeenCalled()
  })
})

describe('useFormSchema can be a pinned version', () => {
  it('reads the given version, under its own cache key', async () => {
    const { Wrapper, queryClient } = createWrapper(client)
    const { result } = renderHook(() => useFormSchema('T', { namespace: 'ns', version: 3 }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.templates.getTemplateByValueAndVersion).toHaveBeenCalledWith('T', 3, { namespace: 'ns' })
    expect(client.templates.getTemplateByValue).not.toHaveBeenCalled()
    expect(queryClient.getQueryData([...wipKeys.templates.byValue('T', 'ns'), 'form-schema', 3])).toBeDefined()
    expect(queryClient.getQueryData([...wipKeys.templates.byValue('T', 'ns'), 'form-schema', 'latest'])).toBeUndefined()
  })
})

describe('writes refresh what they change', () => {
  function invalidated(spy: ReturnType<typeof vi.spyOn>): unknown[][] {
    return spy.mock.calls.map((c: unknown[]) => (c[0] as { queryKey: unknown[] }).queryKey)
  }

  it('a document create refreshes file lists and details and the Registry, not download URLs', async () => {
    const { Wrapper, queryClient } = createWrapper(client)
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useCreateDocument(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ template_id: 'T', namespace: 'ns', data: {} } as never)
    })
    const keys = invalidated(spy)
    expect(keys).toContainEqual([...wipKeys.files.all, 'list'])
    expect(keys).toContainEqual([...wipKeys.files.all, 'detail'])
    expect(keys).toContainEqual(wipKeys.registry.all)
    expect(keys).not.toContainEqual(wipKeys.files.all)
  })

  it('a document update refreshes file views', async () => {
    const { Wrapper, queryClient } = createWrapper(client)
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useUpdateDocument(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ documentId: 'd1', patch: { a: 1 } })
    })
    expect(invalidated(spy)).toContainEqual([...wipKeys.files.all, 'list'])
  })

  it('a template create refreshes Registry searches on screen', async () => {
    const { Wrapper, queryClient } = createWrapper(client)
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useCreateTemplate(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ value: 'T', namespace: 'ns', fields: [] } as never)
    })
    const call = spy.mock.calls.find((c: unknown[]) => (c[0] as { queryKey: unknown[] }).queryKey === wipKeys.registry.all)
    expect(call?.[0]).toMatchObject({ refetchType: 'active' })
  })
})

describe('useBulkImport progress timer', () => {
  it('a new run cancels the previous run\'s clear, and unmount cancels it too', async () => {
    // The hook's own timer is the 2 s one that clears a finished run's progress.
    const realSet = globalThis.setTimeout
    const ours: unknown[] = []
    vi.spyOn(globalThis, 'setTimeout').mockImplementation(((fn: () => void, ms?: number, ...rest: unknown[]) => {
      const handle = realSet(fn, ms, ...rest)
      if (ms === 2000) ours.push(handle)
      return handle
    }) as typeof setTimeout)
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout')
    const cancelled = () => clearSpy.mock.calls.filter((c) => ours.includes(c[0])).map((c) => c[0])

    const { Wrapper } = createWrapper(client)
    const writeFn = vi.fn().mockResolvedValue({ results: [], total: 1, succeeded: 1, failed: 0 })
    const { result, unmount } = renderHook(() => useBulkImport({ writeFn }), { wrapper: Wrapper })

    await act(async () => {
      await result.current.mutateAsync([1])
    })
    await waitFor(() => expect(ours).toHaveLength(1))

    await act(async () => {
      await result.current.mutateAsync([2])
    })
    // The first run's pending clear is cancelled when the second starts.
    expect(cancelled()).toContain(ours[0])
    await waitFor(() => expect(ours).toHaveLength(2))

    unmount()
    expect(cancelled()).toContain(ours[1])
  })

  it('reset keeps the same function across renders', () => {
    const { Wrapper } = createWrapper(client)
    const writeFn = vi.fn()
    const { result, rerender } = renderHook(() => useBulkImport({ writeFn }), { wrapper: Wrapper })
    const first = result.current.reset
    rerender()
    expect(result.current.reset).toBe(first)
  })
})
