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
  TableViewParams,
  TableViewResponse,
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

/**
 * Fetch one document: its latest version, or `version`. `namespace` scopes a
 * value-form identifier for a key that can see several namespaces (a single-
 * namespace key's namespace is derived by the server; a UUID needs none).
 */
export function useDocument(
  id: string,
  options?: { version?: number; namespace?: string } & Omit<UseQueryOptions<Document>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  const { version, namespace, ...queryOptions } = options ?? {}
  return useQuery({
    // Under detail(id), so invalidating detail(id) covers every scope.
    queryKey: [...wipKeys.documents.detail(id), { version: version ?? null, namespace: namespace ?? null }] as const,
    queryFn: () => client.documents.getDocument(id, version, namespace),
    staleTime: STALE_TIMES.documents,
    enabled: !!id,
    ...queryOptions,
  })
}

/**
 * Run a document query. `namespace` scopes the query's template_id (and any
 * value-form identifiers) for a key that can see several namespaces; it
 * cannot ride in the body, which refuses unknown fields.
 */
export function useQueryDocuments(
  query: DocumentQueryRequest,
  options?: { namespace?: string } & Omit<UseQueryOptions<DocumentListResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  const { namespace, ...queryOptions } = options ?? {}
  return useQuery({
    queryKey: [...wipKeys.documents.all, 'query', query, namespace ?? null] as const,
    queryFn: () => client.documents.queryDocuments(query, namespace),
    staleTime: STALE_TIMES.documents,
    // Any query the server takes runs, a status- or sort-only one included.
    // A query that names template_id but has none yet (a template still
    // loading) waits for it; pass `enabled` to gate otherwise.
    enabled: !('template_id' in query) || !!query.template_id,
    ...queryOptions,
  })
}

/**
 * Spreadsheet-style table view of a template's documents.
 *
 * Wraps `client.documents.getTableView` (document-store
 * `GET /table/{template_id}`): column definitions plus one row per
 * document, the same projection the CSV export uses. The queryKey is
 * `wipKeys.documents.tableView(templateId, params)` — declared since the
 * key hierarchy shipped, consumed by a first-party hook only now.
 *
 * Disabled when `templateId` is empty/falsy.
 */
export function useTableView(
  templateId: string,
  params?: TableViewParams,
  options?: Omit<UseQueryOptions<TableViewResponse>, 'queryKey' | 'queryFn'>,
) {
  const client = useWipClient()
  return useQuery({
    queryKey: wipKeys.documents.tableView(templateId, params),
    queryFn: () => client.documents.getTableView(templateId, params),
    staleTime: STALE_TIMES.documents,
    enabled: !!templateId,
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
