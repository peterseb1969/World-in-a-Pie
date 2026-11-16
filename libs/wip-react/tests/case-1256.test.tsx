/**
 * @wip/react hooks take every parameter their client method takes, key their
 * caches on it, and invalidate on settle where a failure can leave writes
 * behind:
 * - useDocument (version, namespace), useQueryDocuments (namespace),
 *   useTerms (params.namespace), useTerm (namespace, terminology) and
 *   useTerminology (namespace) forward the scope, and two scopes of one id
 *   are two cache entries;
 * - useBulkImport invalidates when a batch request fails, not only when every item lands;
 * - useTriggerBatchSyncAll forwards namespace, which force requires;
 * - useDeleteNamespace invalidates every cache, not only the registry's.
 */
import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { WipClient } from '@wip/client'

import { WipProvider } from '../src/provider'
import { wipKeys } from '../src/utils/keys'
import { useDocument, useQueryDocuments } from '../src/hooks/use-documents'
import { useTerm, useTerms } from '../src/hooks/use-terms'
import { useTerminology } from '../src/hooks/use-terminologies'
import { useBulkImport } from '../src/hooks/use-bulk-import'
import { useTriggerBatchSyncAll } from '../src/hooks/use-reporting'
import { useDeleteNamespace } from '../src/hooks/use-mutations'

function createMockClient() {
  return {
    documents: {
      getDocument: vi.fn().mockResolvedValue({ document_id: 'd1' }),
      queryDocuments: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    },
    defStore: {
      listTerms: vi.fn().mockResolvedValue({ items: [], total: 0 }),
      getTerm: vi.fn().mockResolvedValue({ term_id: 't1' }),
      getTerminology: vi.fn().mockResolvedValue({ terminology_id: 'T1' }),
    },
    reporting: { triggerBatchSyncAll: vi.fn().mockResolvedValue([]) },
    registry: { deleteNamespace: vi.fn().mockResolvedValue({ status: 'completed' }) },
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

describe('read hooks forward the scope and key on it', () => {
  it('useDocument forwards version and namespace', async () => {
    const client = createMockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useDocument('DOC-1', { version: 2, namespace: 'ns-a' }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.documents.getDocument).toHaveBeenCalledWith('DOC-1', 2, 'ns-a')
  })

  it('two namespaces of one document id are two cache entries, both under detail(id)', async () => {
    const client = createMockClient()
    const { Wrapper, queryClient } = createWrapper(client)
    const a = renderHook(() => useDocument('DOC-1', { namespace: 'ns-a' }), { wrapper: Wrapper })
    const b = renderHook(() => useDocument('DOC-1', { namespace: 'ns-b' }), { wrapper: Wrapper })
    await waitFor(() => expect(a.result.current.isSuccess && b.result.current.isSuccess).toBe(true))
    expect(client.documents.getDocument).toHaveBeenCalledTimes(2)
    expect(queryClient.getQueryCache().findAll({ queryKey: wipKeys.documents.detail('DOC-1') })).toHaveLength(2)
  })

  it('useQueryDocuments forwards namespace and keys on it', async () => {
    const client = createMockClient()
    const { Wrapper, queryClient } = createWrapper(client)
    const query = { template_id: 'PERSON' }
    const a = renderHook(() => useQueryDocuments(query, { namespace: 'ns-a' }), { wrapper: Wrapper })
    const b = renderHook(() => useQueryDocuments(query, { namespace: 'ns-b' }), { wrapper: Wrapper })
    await waitFor(() => expect(a.result.current.isSuccess && b.result.current.isSuccess).toBe(true))
    expect(client.documents.queryDocuments).toHaveBeenCalledWith(query, 'ns-a')
    expect(client.documents.queryDocuments).toHaveBeenCalledWith(query, 'ns-b')
    expect(queryClient.getQueryCache().findAll({ queryKey: [...wipKeys.documents.all, 'query'] })).toHaveLength(2)
  })

  it('useTerms forwards params.namespace', async () => {
    const client = createMockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useTerms('COUNTRY', { namespace: 'ns-a' }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.defStore.listTerms).toHaveBeenCalledWith('COUNTRY', { namespace: 'ns-a' })
  })

  it('useTerm forwards namespace and terminology', async () => {
    const client = createMockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(
      () => useTerm('United States', { terminology: 'COUNTRY', namespace: 'ns-a' }), { wrapper: Wrapper },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.defStore.getTerm).toHaveBeenCalledWith('United States', { namespace: 'ns-a', terminology: 'COUNTRY' })
  })

  it('useTerm without a scope calls the client as before', async () => {
    const client = createMockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useTerm('t1'), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.defStore.getTerm).toHaveBeenCalledWith('t1', undefined)
  })

  it('useTerminology forwards namespace', async () => {
    const client = createMockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useTerminology('COUNTRY', { namespace: 'ns-a' }), { wrapper: Wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.defStore.getTerminology).toHaveBeenCalledWith('COUNTRY', { namespace: 'ns-a' })
  })
})

describe('invalidation where a failure leaves writes behind', () => {
  it('useBulkImport invalidates when a batch request fails, and reports the batch', async () => {
    const client = createMockClient()
    const { Wrapper, queryClient } = createWrapper(client)
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    const writeFn = vi.fn().mockRejectedValue(new Error('503'))
    const { result } = renderHook(() => useBulkImport({ writeFn }), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate([{ a: 1 }])
    })
    // The real bulkImport resolves: the failed batch is counted and listed.
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.failed).toBe(1)
    expect(result.current.data?.rejected).toHaveLength(1)
    expect(spy).toHaveBeenCalledWith({ queryKey: wipKeys.all })
  })

  it('useDeleteNamespace invalidates every cache', async () => {
    const client = createMockClient()
    const { Wrapper, queryClient } = createWrapper(client)
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useDeleteNamespace(), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate({ prefix: 'gone' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(spy).toHaveBeenCalledWith({ queryKey: wipKeys.all })
  })

  it('useDeleteNamespace invalidates on failure too, and calls the caller onSettled', async () => {
    const client = createMockClient()
    ;(client.registry.deleteNamespace as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('partway'))
    const { Wrapper, queryClient } = createWrapper(client)
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    const onSettled = vi.fn()
    const { result } = renderHook(() => useDeleteNamespace({ onSettled }), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate({ prefix: 'gone' })
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(spy).toHaveBeenCalledWith({ queryKey: wipKeys.all })
    expect(onSettled).toHaveBeenCalled()
  })
})

describe('useTriggerBatchSyncAll', () => {
  it('forwards namespace, which force requires', async () => {
    const client = createMockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useTriggerBatchSyncAll(), { wrapper: Wrapper })
    await act(async () => {
      result.current.mutate({ force: true, namespace: 'ns-a' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(client.reporting.triggerBatchSyncAll).toHaveBeenCalledWith({ force: true, namespace: 'ns-a' })
  })
})
