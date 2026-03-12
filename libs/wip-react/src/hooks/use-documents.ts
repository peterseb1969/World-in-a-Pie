import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type { DocumentListResponse, Document, DocumentQueryParams, DocumentVersionResponse } from '@wip/client'
import { useWipClient } from '../provider.js'
import { wipKeys } from '../utils/keys.js'
import { STALE_TIMES } from '../utils/defaults.js'

export function useDocuments(
  params?: DocumentQueryParams,
  options?: Omit<UseQueryOptions<DocumentListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.documents.list(params),
    queryFn: () => client.documents.listDocuments(params),
    staleTime: STALE_TIMES.documents,
    ...options,
  })
}

export function useDocument(
  id: string,
  options?: Omit<UseQueryOptions<Document>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.documents.detail(id),
    queryFn: () => client.documents.getDocument(id),
    staleTime: STALE_TIMES.documents,
    enabled: !!id,
    ...options,
  })
}

export function useDocumentVersions(
  id: string,
  options?: Omit<UseQueryOptions<DocumentVersionResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.documents.versions(id),
    queryFn: () => client.documents.getVersions(id),
    staleTime: STALE_TIMES.documents,
    enabled: !!id,
    ...options,
  })
}
