import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import { templateToFormSchema, type FormField } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

/**
 * Fetch a template by value and convert it to a framework-agnostic form schema.
 */
export function useFormSchema(
  templateValue: string,
  options?: Omit<UseQueryOptions<FormField[]>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: [...wipKeys.templates.byValue(templateValue), 'form-schema'],
    queryFn: async () => {
      const template = await client.templates.getTemplateByValue(templateValue)
      return templateToFormSchema(template)
    },
    staleTime: STALE_TIMES.templates,
    enabled: !!templateValue,
    ...options,
  })
}
