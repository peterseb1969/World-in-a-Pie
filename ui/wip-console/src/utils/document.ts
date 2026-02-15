/**
 * Get a display title for a document.
 *
 * Convention: if the document data contains a string field named "title"
 * (case-insensitive), use its value. Otherwise fall back to document_id.
 */
export function getDocumentTitle(doc: { document_id: string; data?: Record<string, unknown> }): string {
  if (doc.data) {
    const titleKey = Object.keys(doc.data).find(k => k.toLowerCase() === 'title')
    if (titleKey) {
      const val = doc.data[titleKey]
      if (typeof val === 'string' && val.trim()) {
        return val.trim()
      }
    }
  }
  return doc.document_id
}

/**
 * Check whether a document has a title field.
 */
export function hasDocumentTitle(doc: { data?: Record<string, unknown> }): boolean {
  if (!doc.data) return false
  const titleKey = Object.keys(doc.data).find(k => k.toLowerCase() === 'title')
  return !!titleKey && typeof doc.data[titleKey] === 'string' && !!doc.data[titleKey]
}
