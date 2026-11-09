import { type Request, type Response } from 'express'
import { IDENTITY_HEADERS, requestFnFor, streamUpstreamResponse } from './api-proxy.js'

/** Redirect hops followed server-side before giving up. */
const MAX_REDIRECTS = 5

export interface FileProxyOptions {
  /** WIP instance base URL (e.g., 'https://localhost:8443') */
  baseUrl: string
  /** API key for upstream requests */
  apiKey: string
  /** Forward X-WIP-User and X-WIP-Groups from the incoming request (see ApiProxyOptions) */
  forwardIdentity?: boolean
}

/**
 * Proxy file content downloads.
 *
 * Route: GET /files/:fileId/content
 *
 * The WIP document-store file content endpoint returns the actual file
 * bytes. This proxy forwards the request with auth, follows any redirect
 * server-side (the endpoint may redirect to internal object-storage URLs — the
 * browser must never see them), and pipes the response through, so file
 * downloads are O(1) in proxy memory regardless of file size. GET-only,
 * so following redirects is safe — there is no request body to replay.
 *
 * The key and identity headers go only to WIP's own origin: a hop to
 * another origin (object storage's presigned URL needs neither) is made
 * without them. A redirect whose Location cannot be parsed answers 502.
 * The file id is one path segment — an encoded `/` in it stays encoded.
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

  const wipOrigin = new URL(options.baseUrl).origin

  const fetchFrom = (url: URL, redirectsLeft: number): void => {
    const hopHeaders = url.origin === wipOrigin ? headers : {}
    const upstreamReq = requestFnFor(url)(url, { method: 'GET', headers: hopHeaders }, (upstreamRes) => {
      const status = upstreamRes.statusCode ?? 0
      const location = upstreamRes.headers.location
      if (status >= 300 && status < 400 && location) {
        upstreamRes.resume() // drain and discard the redirect body
        if (redirectsLeft <= 0) {
          res.status(502).json({ error: 'Too many upstream redirects' })
          return
        }
        let next: URL
        try {
          next = new URL(location, url)
        } catch {
          res.status(502).json({ error: 'Upstream redirect has an invalid Location' })
          return
        }
        fetchFrom(next, redirectsLeft - 1)
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
    new URL(`${options.baseUrl}/api/document-store/files/${encodeURIComponent(String(fileId))}/content`),
    MAX_REDIRECTS,
  )
}
