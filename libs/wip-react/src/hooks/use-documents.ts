import { useQuery } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'
import type {
  DocumentListResponse,
  Document,
  DocumentQueryParams,
  DocumentQueryRequest,
  DocumentVersionResponse,
  DocumentRelationshipsParams,
  DocumentTraverseParams,
  DocumentTraverseResponse,
} from '@wip/client'
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

export function useQueryDocuments(
  query: DocumentQueryRequest,
  options?: Omit<UseQueryOptions<DocumentListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: [...wipKeys.documents.all, 'query', query] as const,
    queryFn: () => client.documents.queryDocuments(query),
    staleTime: STALE_TIMES.documents,
    enabled: !!(query.template_id || (query.filters && query.filters.length > 0)),
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

/**
 * List relationship documents incident to a document (CASE-296).
 *
 * Wraps `client.documents.getDocumentRelationships`. Returns a
 * paginated list of relationship documents (templates with
 * `usage: 'relationship'`) pointing at (incoming) or from (outgoing)
 * the given document.
 *
 * Disabled when `documentId` is empty/falsy.
 */
export function useDocumentRelationships(
  documentId: string,
  params?: DocumentRelationshipsParams,
  options?: Omit<UseQueryOptions<DocumentListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.documents.relationships(documentId, params),
    queryFn: () => client.documents.getDocumentRelationships(documentId, params),
    staleTime: STALE_TIMES.documents,
    enabled: !!documentId,
    ...options,
  })
}

/**
 * Traverse the relationship graph from a document (CASE-296).
 *
 * Wraps `client.documents.traverseDocuments`. BFS expansion through
 * relationship documents, capped at depth=10 and max_nodes=1000.
 * Check `data.truncated` to detect when a cap fired.
 *
 * Disabled when `documentId` is empty/falsy.
 */
export function useTraverseDocuments(
  documentId: string,
  params?: DocumentTraverseParams,
  options?: Omit<UseQueryOptions<DocumentTraverseResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.documents.traverse(documentId, params),
    queryFn: () => client.documents.traverseDocuments(documentId, params),
    staleTime: STALE_TIMES.documents,
    enabled: !!documentId,
    ...options,
  })
}
