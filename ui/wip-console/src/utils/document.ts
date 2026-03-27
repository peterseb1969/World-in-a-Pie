/**
 * Get a display title for a document.
 *
 * Convention: if the document data contains a string field named "title"
 * or "name" (case-insensitive, checked in that order), use its value.
 * Otherwise fall back to document_id.
 */
export function getDocumentTitle(doc: { document_id: string; data?: Record<string, unknown> }): string {
  if (doc.data) {
    for (const candidate of ['title', 'name']) {
      const key = Object.keys(doc.data).find(k => k.toLowerCase() === candidate)
      if (key) {
        const val = doc.data[key]
        if (typeof val === 'string' && val.trim()) {
          return val.trim()
        }
      }
    }
  }
  return doc.document_id
}

/**
 * Check whether a document has a title or name field.
 */
export function hasDocumentTitle(doc: { data?: Record<string, unknown> }): boolean {
  if (!doc.data) return false
  for (const candidate of ['title', 'name']) {
    const key = Object.keys(doc.data).find(k => k.toLowerCase() === candidate)
    if (key && typeof doc.data[key] === 'string' && !!doc.data[key]) return true
  }
  return false
}
