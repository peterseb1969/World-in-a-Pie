import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { Namespace, RegistrySearchResponse, RegistrySearchParams } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useNamespaces(
  options?: Omit<UseQueryOptions<Namespace[]>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.registry.namespaces(),
    queryFn: () => client.registry.listNamespaces(),
    staleTime: STALE_TIMES.registry,
    ...options,
  })
}

export function useRegistrySearch(
  params: RegistrySearchParams,
  options?: Omit<UseQueryOptions<RegistrySearchResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.registry.search(params),
    queryFn: () => client.registry.unifiedSearch(params),
    staleTime: STALE_TIMES.registry,
    enabled: !!params.q,
    ...options,
  })
}
