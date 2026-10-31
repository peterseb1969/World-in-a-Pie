// When the askBar agent retries its connection to the platform MCP server.
//
// The agent connects in the background after the HTTP server is up, so the app
// serves its UI whether or not MCP is reachable. A platform that is still
// starting (the MCP server answers 503 while it cannot reach the Registry, a
// refused connection, a timeout) is transient: retry with growing delays,
// indefinitely. A rejected key (401/403) will not fix itself by waiting: stop
// and report it as configuration.
//
// No imports on purpose: the tests run this file with plain `node --test`.

export type RetryDecision = 'retry' | 'stop'

// The HTTP status an MCP transport error carries, if any. The SDK's transport
// errors put it in `code` (StreamableHTTPError, SseError); fetch-level failures
// (ECONNREFUSED, timeouts) carry a string code or none.
export function errorStatus(err: unknown): number | null {
  if (!err || typeof err !== 'object') return null
  for (const k of ['code', 'status'] as const) {
    const v = (err as Record<string, unknown>)[k]
    if (typeof v === 'number') return v
  }
  return null
}

export function retryDecision(err: unknown): RetryDecision {
  const status = errorStatus(err)
  return status === 401 || status === 403 ? 'stop' : 'retry'
}

// Delay before retry number `attempt` (0-based): 1 s, 2 s, 4 s, … capped at 30 s.
export function backoffMs(attempt: number, baseMs = 1000, capMs = 30_000): number {
  return Math.min(capMs, baseMs * 2 ** Math.max(0, attempt))
}
