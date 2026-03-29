import { type Request, type Response } from 'express'

export interface ApiProxyOptions {
  /** WIP instance base URL (e.g., 'https://localhost:8443') */
  baseUrl: string
  /** API key injected into upstream requests */
  apiKey: string
  /** Request body size limit (default: '100mb') */
  bodyLimit?: string
  /** Additional headers to forward upstream */
  extraHeaders?: Record<string, string>
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
  // For a router.all('/api/def-store/*') handler mounted at '/wip':
  //   request to /wip/api/def-store/terminologies?page=1
  //   → req.url = /api/def-store/terminologies?page=1
  const url = `${options.baseUrl}${req.url}`

  try {
    const headers: Record<string, string> = {
      'X-API-Key': options.apiKey,
      ...options.extraHeaders,
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
