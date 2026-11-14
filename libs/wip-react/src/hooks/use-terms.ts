import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { TermListResponse, Term } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

/**
 * List a terminology's terms. `params.namespace` scopes a value-form
 * terminology identifier for a key that can see several namespaces.
 */
export function useTerms(
  terminologyId: string,
  params?: { page?: number; page_size?: number; status?: string; search?: string; sort_by?: string; sort_order?: 'asc' | 'desc'; namespace?: string },
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

/**
 * Fetch one term. `id` is its UUID, the fully qualified
 * 'ns:terminology:value' form, or — with `terminology` set — the raw term
 * value (never colon-parsed). The 2-part 'TERMINOLOGY:VALUE' shorthand is
 * refused (422). `namespace` scopes a value-form identifier for a key that
 * can see several namespaces.
 */
export function useTerm(
  id: string,
  options?: { namespace?: string; terminology?: string } & Omit<UseQueryOptions<Term>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  const { namespace, terminology, ...queryOptions } = options ?? {}
  return useQuery({
    // Under detail(id), so invalidating detail(id) covers every scope.
    queryKey: [...wipKeys.terms.detail(id), { namespace: namespace ?? null, terminology: terminology ?? null }] as const,
    queryFn: () => client.defStore.getTerm(
      id, namespace === undefined && terminology === undefined ? undefined : { namespace, terminology },
    ),
    staleTime: STALE_TIMES.terms,
    enabled: !!id,
    ...queryOptions,
  })
}
