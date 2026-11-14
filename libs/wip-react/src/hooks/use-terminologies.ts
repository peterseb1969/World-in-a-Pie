import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { TerminologyListResponse, Terminology } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useTerminologies(
  params?: { page?: number; page_size?: number; status?: string; value?: string; namespace?: string; sort_by?: string; sort_order?: 'asc' | 'desc' },
  options?: Omit<UseQueryOptions<TerminologyListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.terminologies.list(params),
    queryFn: () => client.defStore.listTerminologies(params),
    staleTime: STALE_TIMES.terminologies,
    ...options,
  })
}

/**
 * Fetch one terminology by UUID or value. `namespace` scopes a value for a
 * key that can see several namespaces.
 */
export function useTerminology(
  id: string,
  options?: { namespace?: string } & Omit<UseQueryOptions<Terminology>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  const { namespace, ...queryOptions } = options ?? {}
  return useQuery({
    // Under detail(id), so invalidating detail(id) covers every scope.
    queryKey: [...wipKeys.terminologies.detail(id), { namespace: namespace ?? null }] as const,
    queryFn: () => client.defStore.getTerminology(id, namespace === undefined ? undefined : { namespace }),
    staleTime: STALE_TIMES.terminologies,
    enabled: !!id,
    ...queryOptions,
  })
}
