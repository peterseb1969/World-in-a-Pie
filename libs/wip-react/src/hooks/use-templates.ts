import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { TemplateListResponse, Template } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useTemplates(
  params?: {
    page?: number; page_size?: number; status?: string; extends?: string
    value?: string; latest_only?: boolean; namespace?: string
    sort_by?: string; sort_order?: 'asc' | 'desc'
  },
  options?: Omit<UseQueryOptions<TemplateListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.templates.list(params),
    queryFn: () => client.templates.listTemplates(params),
    staleTime: STALE_TIMES.templates,
    ...options,
  })
}

export function useTemplate(
  id: string,
  options?: Omit<UseQueryOptions<Template>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.templates.detail(id),
    queryFn: () => client.templates.getTemplate(id),
    staleTime: STALE_TIMES.templates,
    enabled: !!id,
    ...options,
  })
}

/**
 * Fetch the latest template with this value.
 *
 * A template value is unique only within a namespace: pass `namespace`
 * whenever the client's key can see more than one, or the lookup returns
 * whichever namespace's template comes first. A scoped lookup does not fall
 * back to the `wip` namespace. Omitting `namespace` is right for a
 * single-namespace key, whose namespace the server derives.
 */
export function useTemplateByValue(
  value: string,
  options?: { namespace?: string } & Omit<UseQueryOptions<Template>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  const { namespace, ...queryOptions } = options ?? {}
  return useQuery({
    queryKey: wipKeys.templates.byValue(value, namespace),
    queryFn: () => client.templates.getTemplateByValue(value, { namespace }),
    staleTime: STALE_TIMES.templates,
    enabled: !!value,
    ...queryOptions,
  })
}
