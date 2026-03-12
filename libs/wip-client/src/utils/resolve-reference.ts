import type { WipClient } from '../client.js'

export interface ResolvedReference {
  documentId: string
  displayValue: string
  identityFields: Record<string, unknown>
}

/**
 * Search for documents matching a reference field's target template.
 * Useful for populating reference field autocomplete.
 */
export async function resolveReference(
  client: WipClient,
  templateId: string,
  searchTerm: string,
  limit: number = 10,
): Promise<ResolvedReference[]> {
  // The documents list endpoint has no search param; use the query endpoint instead
  const docs = await client.documents.queryDocuments({
    template_id: templateId,
    search: searchTerm,
    page_size: limit,
    status: 'active',
  })

  return docs.items.map((doc) => ({
    documentId: doc.document_id,
    displayValue: Object.values(doc.data).filter((v) => typeof v === 'string').join(' - ') || doc.document_id,
    identityFields: doc.data,
  }))
}
