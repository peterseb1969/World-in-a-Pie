import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { TermListResponse, Term } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useTerms(
  terminologyId: string,
  params?: { page?: number; page_size?: number; status?: string; search?: string },
  options?: Omit<UseQueryOptions<TermListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.terms.list(terminologyId, params),
    queryFn: () => client.defStore.listTerms(terminologyId, params),
    staleTime: STALE_TIMES.terms,
    enabled: !!terminologyId,
    ...options,
  })
}

export function useTerm(
  id: string,
  options?: Omit<UseQueryOptions<Term>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.terms.detail(id),
    queryFn: () => client.defStore.getTerm(id),
    staleTime: STALE_TIMES.terms,
    enabled: !!id,
    ...options,
  })
}
