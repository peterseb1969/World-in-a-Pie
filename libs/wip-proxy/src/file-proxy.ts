import { type Request, type Response } from 'express'

/** Identity headers forwarded from gateway auth to WIP services */
const IDENTITY_HEADERS = ['x-wip-user', 'x-wip-groups', 'x-wip-auth-method'] as const

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
 * bytes. This proxy forwards the request with auth, then streams the
 * response to the browser. The browser never sees internal MinIO URLs.
 */
export async function handleFileContent(
  req: Request,
  res: Response,
  options: FileProxyOptions,
): Promise<void> {
  const { fileId } = req.params

  if (!fileId) {
    res.status(400).json({ error: 'fileId is required' })
    return
  }

  const url = `${options.baseUrl}/api/document-store/files/${fileId}/content`

  try {
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

    const upstream = await fetch(url, {
      method: 'GET',
      headers,
      redirect: 'follow',
    })

    if (!upstream.ok) {
      res.status(upstream.status)
      const ct = upstream.headers.get('content-type')
      if (ct) res.setHeader('content-type', ct)
      const body = await upstream.text()
      res.send(body)
      return
    }

    // Forward file response headers
    const ct = upstream.headers.get('content-type')
    if (ct) res.setHeader('content-type', ct)

    const cd = upstream.headers.get('content-disposition')
    if (cd) res.setHeader('content-disposition', cd)

    const cl = upstream.headers.get('content-length')
    if (cl) res.setHeader('content-length', cl)

    // Stream the file content
    const body = await upstream.arrayBuffer()
    res.send(Buffer.from(body))
  } catch (err) {
    console.error(`[@wip/proxy] File proxy error for ${fileId}:`, err)
    res.status(502).json({ error: 'File download failed' })
  }
}
