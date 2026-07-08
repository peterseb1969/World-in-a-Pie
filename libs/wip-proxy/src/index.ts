import { readFileSync } from 'node:fs'
import { Router, raw } from 'express'
import { handleApiProxy, WIP_API_PREFIXES, type ApiProxyOptions } from './api-proxy.js'
import { handleFileContent, type FileProxyOptions } from './file-proxy.js'

export interface WipProxyOptions {
  /** WIP instance base URL (e.g., 'https://localhost:8443') */
  baseUrl: string
  /**
   * API key injected into upstream requests. Provide this OR `apiKeyFile`.
   * When both are set, `apiKeyFile` wins.
   */
  apiKey?: string
  /**
   * Path to a file containing the API key — typically the live wip-deploy
   * secrets file (`~/.wip-deploy/<deployment>/secrets/api-key`), the same
   * source the MCP server resolves via `WIP_API_KEY_FILE`. Read once at
   * construction, so a key rotation / target-redeploy is picked up on app
   * restart instead of stranding a baked, stale `.env` value (CASE-495).
   * Takes precedence over `apiKey`.
   */
  apiKeyFile?: string
  /** Request body size limit (default: '100mb') */
  bodyLimit?: string
  /** Additional headers to forward upstream */
  extraHeaders?: Record<string, string>
  /** Forward X-WIP-User, X-WIP-Groups, X-WIP-Auth-Method from incoming request */
  forwardIdentity?: boolean
  /**
   * Namespace to scope reads to under a multi-namespace (e.g. install admin)
   * key. Appends `?namespace=<value>` to the documents query endpoint when
   * the caller hasn't scoped it — fixes the CASE-457 silent-zero-rows trap
   * without each app re-implementing the middleware. See `ApiProxyOptions`
   * for why the injection is limited to that one endpoint.
   */
  defaultNamespace?: string
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
  const apiKey = resolveApiKey(options)

  const rawBody = raw({ type: '*/*', limit: bodyLimit })

  const apiOptions: ApiProxyOptions = {
    baseUrl: options.baseUrl,
    apiKey,
    bodyLimit,
    extraHeaders: options.extraHeaders,
    forwardIdentity: options.forwardIdentity,
    defaultNamespace: options.defaultNamespace,
  }

  const fileOptions: FileProxyOptions = {
    baseUrl: options.baseUrl,
    apiKey,
    forwardIdentity: options.forwardIdentity,
  }

  // File content proxy — must be before the catch-all API routes
  router.get('/files/:fileId/content', (req, res) => {
    handleFileContent(req, res, fileOptions)
  })

  // API proxy routes — one handler per service prefix. Registered as a RegExp
  // because the two express majors in the peer range disagree on string
  // wildcard syntax: bare `*` (Express 4 / path-to-regexp 0.1.x) throws at
  // registration on Express 5 (path-to-regexp 8.x), and the 5-only `/*splat`
  // form doesn't exist on 4. A RegExp bypasses the string parser on both
  // majors, and the optional `(/.*)?` tail matches the bare prefix
  // (e.g. GET /api/def-store) in the same route. Handlers are unaffected:
  // the upstream path comes from req.url, never from the wildcard capture.
  for (const prefix of WIP_API_PREFIXES) {
    router.all(prefixPattern(prefix), rawBody, (req, res) => {
      handleApiProxy(req, res, apiOptions)
    })
  }

  return router
}

/**
 * Anchored match for a service prefix and everything under it:
 * `/api/def-store`, `/api/def-store/terminologies`, … but NOT
 * `/api/def-store-evil`. Exported for the route-matching tests.
 */
export function prefixPattern(prefix: string): RegExp {
  const escaped = prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`^${escaped}(/.*)?$`)
}

/**
 * Resolve the upstream API key from `apiKeyFile` (preferred — read once at
 * startup, like the MCP server's `WIP_API_KEY_FILE`) or `apiKey`. Throws if
 * neither yields a non-empty key, so a misconfigured proxy fails loudly at
 * construction rather than silently 401-ing every upstream call (CASE-495).
 */
export function resolveApiKey(options: WipProxyOptions): string {
  if (options.apiKeyFile) {
    const key = readFileSync(options.apiKeyFile, 'utf8').trim()
    if (!key) {
      throw new Error(`wipProxy: apiKeyFile '${options.apiKeyFile}' is empty`)
    }
    return key
  }
  if (options.apiKey) return options.apiKey
  throw new Error('wipProxy: one of apiKey or apiKeyFile is required')
}

export { WIP_API_PREFIXES } from './api-proxy.js'
export { appConfigHandler } from './app-config.js'
export type { AppConfigEntries } from './app-config.js'
export type { WipProxyOptions as WipProxyConfig }
export type { ApiProxyOptions, FileProxyOptions }
