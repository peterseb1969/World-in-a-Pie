/**
 * One URL path segment from a value: percent-encoded, so a value carrying
 * `?`, `#`, `%` or a space reaches the service as that value. Unencoded, `#`
 * and everything after it never leave the client, and `?` turns the rest into
 * a query string. A `/` is encoded as well, but the services decode `%2F`
 * before routing, so a value with `/` still cannot be addressed by path. Every service path built from a
 * caller's value goes through this; query parameters are encoded by the
 * request layer.
 */
export function seg(value: string | number): string {
  return encodeURIComponent(String(value))
}
