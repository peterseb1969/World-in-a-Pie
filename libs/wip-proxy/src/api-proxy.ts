import { request as httpRequest, type IncomingMessage } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { type Request, type Response } from 'express'

/** Identity headers forwarded from gateway auth to WIP services */
const IDENTITY_HEADERS = ['x-wip-user', 'x-wip-groups', 'x-wip-auth-method'] as const

/** Upstream response headers forwarded back to the client */
const FORWARDED_RESPONSE_HEADERS = [
  'content-type',
  'content-disposition',
  'content-length',
] as const

export interface ApiProxyOptions {
  /** WIP instance base URL (e.g., 'https://localhost:8443') */
  baseUrl: string
  /** API key injected into upstream requests */
  apiKey: string
  /**
   * @deprecated No effect since the proxy streams request bodies instead of
   * buffering them — there is no buffer for a limit to protect. Accepted for
   * config compatibility.
   */
  bodyLimit?: string
  /** Additional headers to forward upstream */
  extraHeaders?: Record<string, string>
  /** Forward X-WIP-User, X-WIP-Groups, X-WIP-Auth-Method from incoming request */
  forwardIdentity?: boolean
  /**
   * Namespace to scope reads to when the app's key is multi-namespace.
   *
   * wip-deploy dev renders inject the install ADMIN key (multi-namespace),
   * which gets no automatic namespace derivation — a value-form template_id
   * on the documents query endpoint then resolves to nothing and the query
   * silently returns zero rows (CASE-457). When set, this appends
   * `?namespace=<value>` to the one endpoint where it's both needed and
   * accepted, unless the caller already scoped the request.
   *
   * Deliberately narrow: only `/api/document-store/documents/query` accepts
   * the query param AND exhibits the silent-zero failure. Files take
   * namespace as a form field, document writes take it in the body, and a
   * `namespace` in the query body is `extra_forbidden` — so a blanket inject
   * 422s those paths (learned the hard way in WIP-Song, 2026-06-12).
   */
  defaultNamespace?: string
}

/** The one upstream path where `defaultNamespace` is injected. See the
 * `defaultNamespace` doc above for why this allowlist is a single entry. */
const NAMESPACE_INJECT_PATH = '/api/document-store/documents/query'

/**
 * Append `?namespace=` to the upstream URL when `defaultNamespace` is set,
 * the request targets the documents query endpoint, and the caller hasn't
 * already scoped it. Returns `reqUrl` unchanged in every other case.
 */
export function applyDefaultNamespace(
  reqUrl: string,
  defaultNamespace?: string,
): string {
  if (!defaultNamespace) return reqUrl
  if (reqUrl.split('?')[0] !== NAMESPACE_INJECT_PATH) return reqUrl
  if (/[?&]namespace=/.test(reqUrl)) return reqUrl // caller already scoped
  const sep = reqUrl.includes('?') ? '&' : '?'
  return `${reqUrl}${sep}namespace=${encodeURIComponent(defaultNamespace)}`
}

/** WIP service path prefixes that get proxied */
const WIP_API_PREFIXES = [
  '/api/registry',
  '/api/def-store',
  '/api/template-store',
  '/api/document-store',
  '/api/reporting-sync',
  '/api/ingest-gateway',
]

/** `node:http` or `node:https` request function for a target URL. */
export function requestFnFor(url: URL): typeof httpRequest {
  return url.protocol === 'https:' ? httpsRequest : httpRequest
}

/**
 * Forward status and the allow-listed headers of an upstream response, then
 * pipe its body to the client. Shared by the API and file proxies — this is
 * the response half of the streaming contract: bytes flow through as they
 * arrive, so proxy memory is O(1) in payload size.
 */
export function streamUpstreamResponse(
  upstreamRes: IncomingMessage,
  res: Response,
): void {
  res.status(upstreamRes.statusCode ?? 502)
  for (const h of FORWARDED_RESPONSE_HEADERS) {
    const value = upstreamRes.headers[h]
    if (value) res.setHeader(h, value)
  }
  // A failure after bytes have flowed cannot become a tidy error response —
  // destroying the client socket (truncated transfer) is the honest signal.
  upstreamRes.on('error', () => res.destroy())
  upstreamRes.pipe(res)
}

/**
 * Handle a proxied API request by forwarding it to the WIP backend
 * with the API key injected.
 *
 * Both directions stream: the incoming request pipes into the upstream
 * request and the upstream response pipes back out, with backpressure in
 * both directions, so proxying is O(1) in payload size. Deliberately built
 * on `node:http` rather than `fetch` — undici buffers a streamed request
 * body whole because its backpressure never propagates to the source
 * socket, so a fetch-based version looks streamed and still holds the
 * payload (measured on an 808MB archive: +2.4GB buffered, +919MB via
 * fetch duplex, +39MB flat with a real pipe).
 *
 * Redirects are forwarded to the client, not followed: following a 307/308
 * would require replaying a request body this handler has already streamed
 * away. WIP's APIs are served on exact paths; a client that meets a
 * redirect re-issues through the proxy and stays same-origin.
 *
 * When mounted inside a Router via `app.use('/wip', router)`, Express
 * strips the mount prefix from `req.url`. So `req.url` is already the
 * correct upstream path (e.g., `/api/def-store/terminologies?page=1`).
 * The proxy consumes `req` as a raw stream — it must be mounted before any
 * body-parsing middleware covering the same paths, or the body will have
 * been drained before it gets here.
 */
export function handleApiProxy(
  req: Request,
  res: Response,
  options: ApiProxyOptions,
): void {
  // req.url has the path relative to the router mount (includes query string)
  const upstreamPath = applyDefaultNamespace(req.url, options.defaultNamespace)
  const url = new URL(`${options.baseUrl}${upstreamPath}`)

  const headers: Record<string, string> = {
    'X-API-Key': options.apiKey,
    ...options.extraHeaders,
  }

  // Forward gateway identity headers to WIP services
  if (options.forwardIdentity) {
    for (const h of IDENTITY_HEADERS) {
      const value = req.headers[h]
      if (typeof value === 'string') {
        headers[h] = value
      }
    }
  }

  if (req.headers['content-type']) {
    headers['content-type'] = req.headers['content-type'] as string
  }
  // Pass the client's content-length through when it sent one; a request
  // without it goes upstream chunked, which the services accept.
  if (req.headers['content-length']) {
    headers['content-length'] = req.headers['content-length'] as string
  }

  const upstreamReq = requestFnFor(url)(url, { method: req.method, headers }, (upstreamRes) => {
    streamUpstreamResponse(upstreamRes, res)
  })

  upstreamReq.on('error', (err) => {
    console.error(`[@wip/proxy] Proxy error ${req.method} ${req.url}:`, err)
    if (res.headersSent) {
      res.destroy()
    } else {
      res.status(502).json({ error: 'Upstream request failed' })
    }
  })

  // Client gone before the upstream finished → stop pumping upstream.
  res.on('close', () => {
    if (!res.writableEnded) upstreamReq.destroy()
  })

  if (['GET', 'HEAD'].includes(req.method)) {
    upstreamReq.end()
  } else {
    req.pipe(upstreamReq)
  }
}

export { WIP_API_PREFIXES }
