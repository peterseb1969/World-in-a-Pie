import { describe, it, expect, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { WipProvider } from '../src/provider'
import { useRehashIdentities, useRequalifyIdentitySynonyms } from '../src/hooks/use-mutations'
import { wipKeys } from '../src/utils/keys'
import type { WipClient } from '@wip/client'
import type { ReactNode } from 'react'

function rehashResponse(dry_run: boolean) {
  return {
    namespace: 'clinic-a',
    dry_run,
    scanned: 2,
    changed: 1,
    unchanged: 1,
    unresolved: 0,
    collisions: 0,
    errors: 0,
    applied: dry_run ? 0 : 1,
    items: [],
    collision_groups: [],
    reporting_note: '',
  }
}

function requalifyResponse(dry_run: boolean) {
  return {
    namespace: 'clinic-a', dry_run, scanned: 1, requalified: 1,
    unchanged: 0, no_entry: 0, errors: 0, items: [],
  }
}

function createWrapper(client: WipClient) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
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

function mockClient() {
  return {
    documents: {
      rehashIdentities: vi.fn(async (req: { dry_run?: boolean }) =>
        rehashResponse(req.dry_run ?? true)),
      requalifyIdentitySynonyms: vi.fn(async (req: { dry_run?: boolean }) =>
        requalifyResponse(req.dry_run ?? true)),
    },
    setAuth: vi.fn(),
  } as unknown as WipClient
}

describe('useRehashIdentities', () => {
  it('calls client.documents.rehashIdentities with the request', async () => {
    const client = mockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useRehashIdentities(), { wrapper: Wrapper })

    const response = await act(() =>
      result.current.mutateAsync({ namespace: 'clinic-a', template_values: ['T'] }),
    )

    expect(client.documents.rehashIdentities).toHaveBeenCalledWith({
      namespace: 'clinic-a',
      template_values: ['T'],
    })
    expect(response.dry_run).toBe(true)
  })

  it('does not invalidate caches after a dry run', async () => {
    const { Wrapper, queryClient } = createWrapper(mockClient())
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useRehashIdentities(), { wrapper: Wrapper })

    await act(() => result.current.mutateAsync({ namespace: 'clinic-a' }))

    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('invalidates documents and registry after an apply', async () => {
    const { Wrapper, queryClient } = createWrapper(mockClient())
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useRehashIdentities(), { wrapper: Wrapper })

    await act(() => result.current.mutateAsync({ namespace: 'clinic-a', dry_run: false }))

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: wipKeys.documents.all })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: wipKeys.registry.all })
  })

  it('still calls the caller-supplied onSuccess', async () => {
    const onSuccess = vi.fn()
    const { Wrapper } = createWrapper(mockClient())
    const { result } = renderHook(() => useRehashIdentities({ onSuccess }), { wrapper: Wrapper })

    await act(() => result.current.mutateAsync({ namespace: 'clinic-a' }))

    expect(onSuccess).toHaveBeenCalledTimes(1)
  })
})

describe('useRequalifyIdentitySynonyms', () => {
  it('calls client.documents.requalifyIdentitySynonyms with the request', async () => {
    const client = mockClient()
    const { Wrapper } = createWrapper(client)
    const { result } = renderHook(() => useRequalifyIdentitySynonyms(), { wrapper: Wrapper })

    await act(() => result.current.mutateAsync({ namespace: 'clinic-a' }))

    expect(client.documents.requalifyIdentitySynonyms).toHaveBeenCalledWith({
      namespace: 'clinic-a',
    })
  })

  it('invalidates the registry only after an apply', async () => {
    const { Wrapper, queryClient } = createWrapper(mockClient())
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const { result } = renderHook(() => useRequalifyIdentitySynonyms(), { wrapper: Wrapper })

    await act(() => result.current.mutateAsync({ namespace: 'clinic-a' }))
    expect(invalidateSpy).not.toHaveBeenCalled()

    await act(() => result.current.mutateAsync({ namespace: 'clinic-a', dry_run: false }))
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: wipKeys.registry.all })
  })
})
