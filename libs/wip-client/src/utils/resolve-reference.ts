import type { WipClient } from '../client.js'

export interface ResolvedReference {
  documentId: string
  displayValue: string
  identityFields: Record<string, unknown>
}

/**
 * Search for documents matching a reference field's target template.
 * Useful for populating reference field autocomplete.
 *
 * Fetches recent documents for the template and filters client-side
 * by search term. For large datasets, consider adding server-side
 * search to the document query endpoint.
 *
 * A template given by value is resolved in `namespace`. A key scoped to one
 * namespace may omit it; a key that can read several must pass it (or a
 * canonical template id), as for `queryDocuments`.
 */
export async function resolveReference(
  client: WipClient,
  templateId: string,
  searchTerm: string,
  limit: number = 10,
  namespace?: string,
): Promise<ResolvedReference[]> {
  // Fetch recent documents for this template
  const docs = await client.documents.queryDocuments(
    {
      template_id: templateId,
      page_size: 100,
      status: 'active',
      sort_by: 'created_at',
      sort_order: 'desc',
    },
    namespace,
  )

  // Client-side filter by search term across data values
  const term = searchTerm.toLowerCase()
  const matched = docs.items
    .filter((doc) =>
      Object.values(doc.data).some(
        (v) => typeof v === 'string' && v.toLowerCase().includes(term),
      ),
    )
    .slice(0, limit)

  return matched.map((doc) => ({
    documentId: doc.document_id,
    displayValue:
      Object.values(doc.data)
        .filter((v) => typeof v === 'string')
        .join(' - ') || doc.document_id,
    identityFields: doc.data,
  }))
}
