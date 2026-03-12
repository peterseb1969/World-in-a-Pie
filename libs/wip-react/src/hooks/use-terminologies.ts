import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { TerminologyListResponse, Terminology } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useTerminologies(
  params?: { page?: number; page_size?: number; status?: string; value?: string; namespace?: string },
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

export function useTerminology(
  id: string,
  options?: Omit<UseQueryOptions<Terminology>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.terminologies.detail(id),
    queryFn: () => client.defStore.getTerminology(id),
    staleTime: STALE_TIMES.terminologies,
    enabled: !!id,
    ...options,
  })
}
