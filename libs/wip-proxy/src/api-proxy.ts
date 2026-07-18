import { type Request, type Response } from 'express'

/** Identity headers forwarded from gateway auth to WIP services */
const IDENTITY_HEADERS = ['x-wip-user', 'x-wip-groups', 'x-wip-auth-method'] as const

export interface ApiProxyOptions {
  /** WIP instance base URL (e.g., 'https://localhost:8443') */
  baseUrl: string
  /** API key injected into upstream requests */
  apiKey: string
  /** Request body size limit (default: '100mb') */
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

/**
 * Handle a proxied API request by forwarding it to the WIP backend
 * with the API key injected.
 *
 * When mounted inside a Router via `app.use('/wip', router)`, Express
 * strips the mount prefix from `req.url`. So `req.url` is already the
 * correct upstream path (e.g., `/api/def-store/terminologies?page=1`).
 */
export async function handleApiProxy(
  req: Request,
  res: Response,
  options: ApiProxyOptions,
): Promise<void> {
  // req.url has the path relative to the router mount (includes query string)
  // For the per-prefix route mounted at '/wip':
  //   request to /wip/api/def-store/terminologies?page=1
  //   → req.url = /api/def-store/terminologies?page=1
  const upstreamPath = applyDefaultNamespace(req.url, options.defaultNamespace)
  const url = `${options.baseUrl}${upstreamPath}`

  try {
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

    const upstream = await fetch(url, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : req.body,
    })

    // Forward status
    res.status(upstream.status)

    // Forward response headers
    const ct = upstream.headers.get('content-type')
    if (ct) res.setHeader('content-type', ct)

    const cd = upstream.headers.get('content-disposition')
    if (cd) res.setHeader('content-disposition', cd)

    const cl = upstream.headers.get('content-length')
    if (cl) res.setHeader('content-length', cl)

    // Stream the response body
    const body = await upstream.arrayBuffer()
    res.send(Buffer.from(body))
  } catch (err) {
    console.error(`[@wip/proxy] Proxy error ${req.method} ${req.url}:`, err)
    res.status(502).json({ error: 'Upstream request failed' })
  }
}

export { WIP_API_PREFIXES }
