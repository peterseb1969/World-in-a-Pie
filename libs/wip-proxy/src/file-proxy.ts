import { type Request, type Response } from 'express'
import { requestFnFor, streamUpstreamResponse } from './api-proxy.js'

/** Identity headers forwarded from gateway auth to WIP services */
const IDENTITY_HEADERS = ['x-wip-user', 'x-wip-groups', 'x-wip-auth-method'] as const

/** Redirect hops followed server-side before giving up. */
const MAX_REDIRECTS = 5

export interface FileProxyOptions {
  /** WIP instance base URL (e.g., 'https://localhost:8443') */
  baseUrl: string
  /** API key for upstream requests */
  apiKey: string
  /** Forward X-WIP-User, X-WIP-Groups, X-WIP-Auth-Method from incoming request */
  forwardIdentity?: boolean
}

/**
 * Proxy file content downloads.
 *
 * Route: GET /files/:fileId/content
 *
 * The WIP document-store file content endpoint returns the actual file
 * bytes. This proxy forwards the request with auth, follows any redirect
 * server-side (the endpoint may redirect to internal MinIO URLs — the
 * browser must never see them), and pipes the response through, so file
 * downloads are O(1) in proxy memory regardless of file size. GET-only,
 * so following redirects is safe — there is no request body to replay.
 */
export function handleFileContent(
  req: Request,
  res: Response,
  options: FileProxyOptions,
): void {
  const { fileId } = req.params

  if (!fileId) {
    res.status(400).json({ error: 'fileId is required' })
    return
  }

  const headers: Record<string, string> = { 'X-API-Key': options.apiKey }

  // Forward gateway identity headers to WIP services
  if (options.forwardIdentity) {
    for (const h of IDENTITY_HEADERS) {
      const value = req.headers[h]
      if (typeof value === 'string') {
        headers[h] = value
      }
    }
  }

  const fetchFrom = (url: URL, redirectsLeft: number): void => {
    const upstreamReq = requestFnFor(url)(url, { method: 'GET', headers }, (upstreamRes) => {
      const status = upstreamRes.statusCode ?? 0
      const location = upstreamRes.headers.location
      if (status >= 300 && status < 400 && location) {
        upstreamRes.resume() // drain and discard the redirect body
        if (redirectsLeft <= 0) {
          res.status(502).json({ error: 'Too many upstream redirects' })
          return
        }
        fetchFrom(new URL(location, url), redirectsLeft - 1)
        return
      }
      streamUpstreamResponse(upstreamRes, res)
    })

    upstreamReq.on('error', (err) => {
      console.error(`[@wip/proxy] File proxy error for ${fileId}:`, err)
      if (res.headersSent) {
        res.destroy()
      } else {
        res.status(502).json({ error: 'File download failed' })
      }
    })

    res.on('close', () => {
      if (!res.writableEnded) upstreamReq.destroy()
    })

    upstreamReq.end()
  }

  fetchFrom(
    new URL(`${options.baseUrl}/api/document-store/files/${fileId}/content`),
    MAX_REDIRECTS,
  )
}
