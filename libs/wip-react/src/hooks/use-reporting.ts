import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { IntegrityCheckResult, ActivityResponse } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useIntegrityCheck(
  params?: {
    template_status?: string
    document_status?: string
    template_limit?: number
    document_limit?: number
    check_term_refs?: boolean
    recent_first?: boolean
  },
  options?: Omit<UseQueryOptions<IntegrityCheckResult>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.reporting.integrity(params),
    queryFn: () => client.reporting.getIntegrityCheck(params),
    staleTime: STALE_TIMES.reporting,
    ...options,
  })
}

export function useActivity(
  params?: { types?: string; limit?: number },
  options?: Omit<UseQueryOptions<ActivityResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.reporting.activity(params),
    queryFn: () => client.reporting.getRecentActivity(params),
    staleTime: STALE_TIMES.reporting,
    ...options,
  })
}
