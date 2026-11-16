/**
 * Every write that changes what the Registry holds refreshes the Registry
 * listings on screen, not only creates: a retired entity (deleted,
 * deprecated, its template versions deactivated) releases its synonyms, a
 * reactivated one claims them again, and a hard delete removes the entry.
 */
import { describe, it, expect, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { WipClient } from '@wip/client'

import { WipProvider } from '../src/provider'
import { wipKeys } from '../src/utils/keys'
import {
  useDeleteTerminology,
  useDeprecateTerm,
  useDeleteTerm,
  useDeleteTemplate,
  useActivateTemplate,
  useReactivateTemplate,
  useDeleteDocument,
  useDeleteFile,
  useDeleteFiles,
  useHardDeleteFile,
} from '../src/hooks/use-mutations'

/** A client whose every service method resolves to an empty object. */
function anyClient(): WipClient {
  const service = new Proxy({}, { get: () => vi.fn().mockResolvedValue({}) })
  return new Proxy({ setAuth: vi.fn() }, {
    get: (target, prop) => (prop in target ? target[prop as keyof typeof target] : service),
  }) as unknown as WipClient
}

const CASES: [string, () => { mutateAsync: (v: never) => Promise<unknown> }, unknown][] = [
  ['useDeleteTerminology', () => useDeleteTerminology(), 'tid'],
  ['useDeprecateTerm', () => useDeprecateTerm(), { termId: 't1', data: {} }],
  ['useDeleteTerm', () => useDeleteTerm('tid'), 't1'],
  ['useDeleteTemplate', () => useDeleteTemplate(), { id: 'T' }],
  ['useActivateTemplate', () => useActivateTemplate(), { id: 'T', namespace: 'ns' }],
  ['useReactivateTemplate', () => useReactivateTemplate(), { id: 'T', version: 1, namespace: 'ns' }],
  ['useDeleteDocument', () => useDeleteDocument(), { id: 'd1' }],
  ['useDeleteFile', () => useDeleteFile(), 'f1'],
  ['useDeleteFiles', () => useDeleteFiles(), ['f1']],
  ['useHardDeleteFile', () => useHardDeleteFile(), 'f1'],
]

describe('retiring, reactivating and removing refresh Registry listings', () => {
  it.each(CASES)('%s', async (_name, hook, variables) => {
    const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const spy = vi.spyOn(queryClient, 'invalidateQueries')
    const client = anyClient()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <WipProvider client={client}>{children}</WipProvider>
      </QueryClientProvider>
    )
    const { result } = renderHook(hook, { wrapper })
    await act(async () => {
      await result.current.mutateAsync(variables as never)
    })
    const call = spy.mock.calls.find(
      (c: unknown[]) => (c[0] as { queryKey: unknown[] }).queryKey === wipKeys.registry.all,
    )
    expect(call?.[0]).toMatchObject({ refetchType: 'active' })
  })
})
