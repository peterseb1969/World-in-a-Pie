/** Build a URL query string from a params object, handling undefined, arrays, and booleans. */
export function buildQueryString(params: Record<string, unknown>): string {
  const searchParams = new URLSearchParams()

  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue

    if (Array.isArray(value)) {
      for (const v of value) {
        searchParams.append(key, String(v))
      }
    } else if (typeof value === 'boolean') {
      searchParams.set(key, value ? 'true' : 'false')
    } else {
      searchParams.set(key, String(value))
    }
  }

  const str = searchParams.toString()
  return str ? `?${str}` : ''
}
