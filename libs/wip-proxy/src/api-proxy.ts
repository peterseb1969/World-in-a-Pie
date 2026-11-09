import { request as httpRequest, type IncomingMessage } from 'node:http'
import { request as httpsRequest } from 'node:https'
import { type Request, type Response } from 'express'

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
  /**
   * Namespace for the documents query endpoint when the app's key spans
   * several namespaces.
   *
   * An app key always spans at least its own namespaces plus `wip`, so it
   * gets no automatic namespace derivation: a value-form template_id on
   * `POST /api/document-store/documents/query` is then refused with 422, and
   * a query without one reads every namespace the key can read. When set,
   * this appends `?namespace=<value>` to that one endpoint, unless the caller
   * already scoped the request.
   *
   * Deliberately one endpoint, not every read: other routes take the
   * namespace in the body or a form field, and some refuse unknown query
   * parameters, so a blanket inject breaks them (learned the hard way in
   * WIP-Song, 2026-06-12).
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

/** True for a path under one of the six WIP service prefixes. */
export function isWipApiPath(pathname: string): boolean {
  return WIP_API_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`))
}

/** baseUrl's own path (no trailing slash): '' for a bare origin. */
function basePathOf(baseUrl: URL): string {
  return baseUrl.pathname.replace(/\/+$/, '')
}

/**
 * The upstream URL for a proxied request, or null when the request's path,
 * once normalised, is not under a WIP service prefix.
 *
 * Express matched the route against the raw path, but a URL parser collapses
 * dot segments — `..`, `%2e%2e`, and backslashes, which a special-scheme URL
 * reads as slashes — so `/api/def-store/../../auth/userinfo` would leave as
 * `/auth/userinfo`, and `/api/def-store/../def-store-evil` as a look-alike
 * prefix. The path is therefore normalised first and checked against the
 * prefixes on its own, and the URL is built from the normalised form.
 */
export function upstreamUrlFor(reqUrl: string, options: ApiProxyOptions): URL | null {
  const normalised = new URL(`http://wip.invalid${reqUrl.startsWith('/') ? '' : '/'}${reqUrl}`)
  if (!isWipApiPath(normalised.pathname)) return null
  const path = applyDefaultNamespace(`${normalised.pathname}${normalised.search}`, options.defaultNamespace)
  return new URL(`${options.baseUrl}${path}`)
}

/**
 * Where a client should go for an upstream redirect, or undefined to drop the
 * Location. A redirect onto a WIP API path of this WIP instance is re-issued
 * under the proxy's mount (`/wip/api/registry/`), so the browser stays on the
 * app's origin; anything else (another host, a non-API path) is not handed to
 * the client — it would leave the app's origin or expose WIP's.
 */
export function clientLocation(
  location: string | undefined,
  upstreamUrl: URL,
  baseUrl: string,
  mountPath: string,
): string | undefined {
  if (!location) return undefined
  let target: URL
  try {
    target = new URL(location, upstreamUrl)
  } catch {
    return undefined
  }
  const base = new URL(baseUrl)
  if (target.origin !== base.origin) return undefined
  const basePath = basePathOf(base)
  if (basePath && !target.pathname.startsWith(`${basePath}/`)) return undefined
  const path = target.pathname.slice(basePath.length)
  if (!isWipApiPath(path)) return undefined
  return `${mountPath}${path}${target.search}`
}

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
  extraHeaders: Record<string, string> = {},
): void {
  res.status(upstreamRes.statusCode ?? 502)
  for (const h of FORWARDED_RESPONSE_HEADERS) {
    const value = upstreamRes.headers[h]
    if (value) res.setHeader(h, value)
  }
  for (const [h, value] of Object.entries(extraHeaders)) res.setHeader(h, value)
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
 * away. A redirect onto a WIP API path (e.g. a service's bare prefix →
 * its trailing-slash form) is re-issued under the proxy's mount, so the
 * client follows it through the proxy and stays same-origin; any other
 * Location is dropped (see `clientLocation`).
 *
 * The path is checked after normalisation (see `upstreamUrlFor`): a request
 * whose dot segments would leave the WIP service prefixes is refused with
 * 400 `wip_proxy_path_refused` and never sent.
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
  const url = upstreamUrlFor(req.url, options)
  if (url === null) {
    res.status(400).json({
      error: 'wipProxy: the request path, once normalised, is not under a WIP API prefix',
      error_code: 'wip_proxy_path_refused',
    })
    return
  }

  const headers: Record<string, string> = {
    'X-API-Key': options.apiKey,
    ...options.extraHeaders,
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
    const status = upstreamRes.statusCode ?? 0
    const location = status >= 300 && status < 400
      ? clientLocation(upstreamRes.headers.location, url, options.baseUrl, req.baseUrl)
      : undefined
    streamUpstreamResponse(upstreamRes, res, location ? { location } : {})
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
