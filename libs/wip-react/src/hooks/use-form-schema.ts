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
 *
 * Without `version` the schema is the template's latest version. A form that
 * edits an existing document should pass the document's `template_version`:
 * an update is validated against the version the document is pinned to, so a
 * latest-version form can offer fields that version does not have.
 */
export function useFormSchema(
  templateValue: string,
  options?: { namespace?: string; version?: number } & Omit<UseQueryOptions<FormField[]>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  const { namespace, version, ...queryOptions } = options ?? {}
  return useQuery({
    // The version is part of the key: two versions' schemas must not share a cache entry.
    queryKey: [...wipKeys.templates.byValue(templateValue, namespace), 'form-schema', version ?? 'latest'],
    queryFn: async () => {
      const template = version === undefined
        ? await client.templates.getTemplateByValue(templateValue, { namespace })
        : await client.templates.getTemplateByValueAndVersion(templateValue, version, { namespace })
      return templateToFormSchema(template)
    },
    staleTime: STALE_TIMES.templates,
    enabled: !!templateValue,
    ...queryOptions,
  })
}
