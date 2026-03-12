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

export function useTemplateByValue(
  value: string,
  options?: Omit<UseQueryOptions<Template>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.templates.byValue(value),
    queryFn: () => client.templates.getTemplateByValue(value),
    staleTime: STALE_TIMES.templates,
    enabled: !!value,
    ...options,
  })
}
