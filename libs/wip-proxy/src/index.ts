import { Router, raw } from 'express'
import { handleApiProxy, WIP_API_PREFIXES, type ApiProxyOptions } from './api-proxy.js'
import { handleFileContent, type FileProxyOptions } from './file-proxy.js'

export interface WipProxyOptions {
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
}

/**
 * Create an Express router that proxies WIP API calls and file downloads.
 *
 * Usage:
 * ```ts
 * import { wipProxy } from '@wip/proxy'
 *
 * app.use('/wip', wipProxy({
 *   baseUrl: process.env.WIP_BASE_URL || 'https://localhost:8443',
 *   apiKey: process.env.WIP_API_KEY,
 * }))
 * ```
 *
 * This creates:
 * - `GET|POST|PUT|DELETE /wip/api/{service}/*` — proxied to WIP with API key
 * - `GET /wip/files/:fileId/content` — proxied file download (resolves MinIO URLs server-side)
 */
export function wipProxy(options: WipProxyOptions): Router {
  const router = Router()
  const bodyLimit = options.bodyLimit || '100mb'

  const rawBody = raw({ type: '*/*', limit: bodyLimit })

  const apiOptions: ApiProxyOptions = {
    baseUrl: options.baseUrl,
    apiKey: options.apiKey,
    bodyLimit,
    extraHeaders: options.extraHeaders,
    forwardIdentity: options.forwardIdentity,
  }

  const fileOptions: FileProxyOptions = {
    baseUrl: options.baseUrl,
    apiKey: options.apiKey,
    forwardIdentity: options.forwardIdentity,
  }

  // File content proxy — must be before the catch-all API routes
  router.get('/files/:fileId/content', (req, res) => {
    handleFileContent(req, res, fileOptions)
  })

  // API proxy routes — one handler per service prefix
  for (const prefix of WIP_API_PREFIXES) {
    router.all(`${prefix}/*`, rawBody, (req, res) => {
      handleApiProxy(req, res, apiOptions)
    })
    // Also handle the prefix itself (e.g., GET /api/def-store)
    router.all(prefix, rawBody, (req, res) => {
      handleApiProxy(req, res, apiOptions)
    })
  }

  return router
}

export { WIP_API_PREFIXES } from './api-proxy.js'
export type { WipProxyOptions as WipProxyConfig }
export type { ApiProxyOptions, FileProxyOptions }
