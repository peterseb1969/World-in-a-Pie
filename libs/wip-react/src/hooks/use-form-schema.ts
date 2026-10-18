import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import { templateToFormSchema, type FormField } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

/**
 * Fetch a template by value and convert it to a framework-agnostic form schema.
 *
 * Pass `namespace` whenever the client's key can see more than one namespace
 * (a template value is unique only within a namespace) — see
 * `useTemplateByValue`.
 */
export function useFormSchema(
  templateValue: string,
  options?: { namespace?: string } & Omit<UseQueryOptions<FormField[]>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  const { namespace, ...queryOptions } = options ?? {}
  return useQuery({
    queryKey: [...wipKeys.templates.byValue(templateValue, namespace), 'form-schema'],
    queryFn: async () => {
      const template = await client.templates.getTemplateByValue(templateValue, { namespace })
      return templateToFormSchema(template)
    },
    staleTime: STALE_TIMES.templates,
    enabled: !!templateValue,
    ...queryOptions,
  })
}
