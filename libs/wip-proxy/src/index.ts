import { readFileSync } from 'node:fs'
import { Router, type Request, type Response } from 'express'
import { handleApiProxy, WIP_API_PREFIXES, type ApiProxyOptions } from './api-proxy.js'
import { handleFileContent, type FileProxyOptions } from './file-proxy.js'

/**
 * Supplies the API key for an upstream request. Called on every proxied
 * request; a null, undefined or empty result means "no key now".
 */
export type ApiKeyProvider = () => string | null | undefined | Promise<string | null | undefined>

/**
 * Throw from an `ApiKeyProvider` to say why there is no key: its message is
 * shown in the 503 body and on `unavailableReason`, so it must name the
 * source (a path, a setting), never a key. Any other error a provider throws
 * is reported only as "the getApiKey provider failed".
 */
export class KeySourceError extends Error {}

export interface WipProxyOptions {
  /** WIP instance base URL (e.g., 'https://localhost:8443') */
  baseUrl: string
  /**
   * API key injected into upstream requests. Provide this, `apiKeyFile`, or
   * `getApiKey`. When `apiKeyFile` is also set, `apiKeyFile` wins.
   */
  apiKey?: string
  /**
   * Path to a file containing the API key — typically the app's own key
   * file (`WIP_API_KEY_FILE`, e.g. a mounted Secret), never the install's
   * master key. Read once at construction, so a rotation is picked up on app
   * restart; for a rotation without a restart, use
   * `getApiKey: keyFromFile(path)` instead. Takes precedence over `apiKey`.
   */
  apiKeyFile?: string
  /**
   * A key provider, asked on every proxied request — for a key that appears
   * or changes while the app runs (a settings page, a Secret the kubelet
   * refreshes in place; see `keyFromFile`). When given it is the ONLY
   * source: `apiKey` and `apiKeyFile` are ignored (with one warning at
   * construction), and there is no fallback to them. Its result is trimmed
   * and checked like a static key. No key, a key with whitespace or a
   * control character, or a provider that throws answers that request 503
   * `wip_proxy_unavailable` naming the source, never the value — and the
   * next request asks again, so the proxy recovers as soon as a key exists.
   * `onMissingKey` does not apply.
   */
  getApiKey?: ApiKeyProvider
  /**
   * @deprecated No effect since 0.5.0: request and response bodies stream
   * through the proxy instead of being buffered, so there is no buffer for
   * a limit to protect. Accepted (and ignored) for config compatibility.
   */
  bodyLimit?: string
  /** Additional headers to forward upstream */
  extraHeaders?: Record<string, string>
  /**
   * Forward X-WIP-User and X-WIP-Groups from the incoming request. WIP acts
   * on them only when the app's key is privileged.
   */
  forwardIdentity?: boolean
  /**
   * Namespace appended as `?namespace=<value>` to the documents query
   * endpoint (`POST /api/document-store/documents/query`, and only there)
   * when the caller hasn't scoped it — needed under a key spanning several
   * namespaces, which every app key does. See `ApiProxyOptions`.
   */
  defaultNamespace?: string
  /**
   * What happens when neither `apiKeyFile` nor `apiKey` yields a key (not
   * used with `getApiKey`, which is asked per request).
   *
   * - `'throw'` (default): `wipProxy()` throws at construction, naming every
   *   attempted source. A misconfigured proxy fails loudly.
   * - `'unavailable'`: `wipProxy()` returns a router whose routes all answer
   *   503 `{"error": <reason>, "error_code": "wip_proxy_unavailable"}`, and
   *   logs one warning. A missing key then costs the `/wip` mount, not the
   *   app: called at module load, the throw would stop the process before
   *   `listen()`, taking `/api/health` with it.
   *
   * The reason is the exact message `'throw'` raises, in the 503 body and on
   * the returned router's `unavailableReason`, so a degraded app reports the
   * same diagnosis as a crashed one. Keep the two identical.
   */
  onMissingKey?: 'throw' | 'unavailable'
}

/**
 * The router `wipProxy()` returns. `unavailableReason` is null when the proxy
 * works, and the missing-key message when it was built with
 * `onMissingKey: 'unavailable'` and no key — so an app can surface it, e.g.
 * as `wip_proxy` in its `appConfigHandler` entries. With `getApiKey` it
 * reports the outcome of the most recent request's key resolution (null
 * before the first request and whenever a key was found).
 */
export type WipProxyRouter = Router & { readonly unavailableReason: string | null }

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
 * - `/wip/api/{service}/*`, every method — proxied to WIP with the API key
 * - `GET /wip/files/:fileId/content` — proxied file download (resolves object-storage URLs server-side)
 *
 * Both directions STREAM — proxy memory is O(1) in payload size, so large
 * archive uploads/downloads pass through without buffering. Consequence:
 * the proxy consumes the request as a raw stream, so it must be mounted
 * BEFORE any body-parsing middleware (`express.json()`, `express.raw()`, …)
 * that would match the same paths — a parser ahead of the proxy drains the
 * body, and the upstream request then never completes.
 */
export function wipProxy(options: WipProxyOptions): WipProxyRouter {
  if (options.getApiKey) return providedKeyProxy(options, options.getApiKey)
  let apiKey: string
  try {
    apiKey = resolveApiKey(options)
  } catch (err) {
    if (options.onMissingKey !== 'unavailable') throw err
    return unavailableProxy(err instanceof Error ? err.message : String(err))
  }
  const router = proxyRouter(options, (req, res, handle) => handle(apiKey))
  return Object.assign(router, { unavailableReason: null })
}

/**
 * Runs a proxy handler with the key for this request, or answers the request
 * itself (503) when there is none.
 */
type WithKey = (req: Request, res: Response, handle: (apiKey: string) => void) => void

function proxyRouter(options: WipProxyOptions, withKey: WithKey): Router {
  const router = Router()
  const apiOptions = (apiKey: string): ApiProxyOptions => ({
    baseUrl: options.baseUrl,
    apiKey,
    extraHeaders: options.extraHeaders,
    forwardIdentity: options.forwardIdentity,
    defaultNamespace: options.defaultNamespace,
  })
  const fileOptions = (apiKey: string): FileProxyOptions => ({
    baseUrl: options.baseUrl,
    apiKey,
    forwardIdentity: options.forwardIdentity,
  })

  // File content proxy — must be before the catch-all API routes
  router.get('/files/:fileId/content', (req, res) => {
    withKey(req, res, (apiKey) => handleFileContent(req, res, fileOptions(apiKey)))
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
    router.all(prefixPattern(prefix), (req, res) => {
      withKey(req, res, (apiKey) => handleApiProxy(req, res, apiOptions(apiKey)))
    })
  }
  return router
}

function answerUnavailable(res: Response, reason: string): void {
  res.status(503).json({ error: reason, error_code: 'wip_proxy_unavailable' })
}

/**
 * A proxy whose key comes from `getApiKey`, asked per request. The request
 * stream is not read until the key is known, so a streamed body waits in the
 * socket. A rejection never escapes into Express (Express 4 would leave the
 * request hanging): every outcome answers the request.
 */
function providedKeyProxy(options: WipProxyOptions, getApiKey: ApiKeyProvider): WipProxyRouter {
  const ignored = [options.apiKeyFile && 'apiKeyFile', options.apiKey && 'apiKey'].filter(Boolean)
  if (ignored.length) {
    console.warn(
      `wipProxy: getApiKey is set, so ${ignored.join(' and ')} ${ignored.length > 1 ? 'are' : 'is'} ignored — ` +
      'the provider is the only key source.',
    )
  }
  let lastReason: string | null = null
  const withKey: WithKey = (_req, res, handle) => {
    resolveProvidedKey(getApiKey).then((outcome) => {
      if (outcome.reason !== lastReason && outcome.reason !== null) {
        console.warn(`wipProxy: ${outcome.reason} — /wip answers 503 until it yields a key.`)
      }
      lastReason = outcome.reason
      if (outcome.key === null) answerUnavailable(res, outcome.reason!)
      else handle(outcome.key)
    }, () => answerUnavailable(res, 'wipProxy: the getApiKey provider failed'))
  }
  const router = proxyRouter(options, withKey)
  return Object.defineProperty(router, 'unavailableReason', {
    get: () => lastReason,
    enumerable: true,
  }) as WipProxyRouter
}

type KeyOutcome = { key: string; reason: null } | { key: null; reason: string }

async function resolveProvidedKey(getApiKey: ApiKeyProvider): Promise<KeyOutcome> {
  let value: string | null | undefined
  try {
    value = await getApiKey()
  } catch (err) {
    const why = err instanceof KeySourceError ? `: ${err.message}` : ''
    return { key: null, reason: `wipProxy: the getApiKey provider failed${why}` }
  }
  const key = typeof value === 'string' ? value.trim() : ''
  if (!key) return { key: null, reason: 'wipProxy: the getApiKey provider returned no key' }
  if (!isCleanKey(key)) {
    return {
      key: null,
      reason: 'wipProxy: the getApiKey provider returned a key containing whitespace or a control character; WIP keys never do',
    }
  }
  return { key, reason: null }
}

/**
 * A key provider reading `path`, re-read at most once per `ttlMs` (default
 * 15 s) — for a Secret mounted as a file, which the kubelet refreshes in
 * place, so a rotation reaches the running app within its refresh delay plus
 * `ttlMs`. A missing, unreadable or empty file means "no key now" and names
 * the path in the 503; the next read after `ttlMs` tries again. The key's
 * content never appears in any message.
 */
export function keyFromFile(path: string, { ttlMs = 15_000 }: { ttlMs?: number } = {}): ApiKeyProvider {
  let cached: { at: number; key: string } | null = null
  return () => {
    const now = Date.now()
    if (cached && now - cached.at < ttlMs) return cached.key
    let key: string
    try {
      key = readFileSync(path, 'utf8').trim()
    } catch (err) {
      cached = null
      const code = (err as NodeJS.ErrnoException).code ?? 'unreadable'
      throw new KeySourceError(`key file '${path}' is unreadable (${code})`)
    }
    if (!key) {
      cached = null
      throw new KeySourceError(`key file '${path}' is empty`)
    }
    cached = { at: now, key }
    return key
  }
}

/**
 * The proxy's routes, each answering 503 with the reason. Registered exactly
 * like the working proxy's, so a path it never served still falls through to
 * the app as before. The handlers do not read the request body.
 */
function unavailableProxy(reason: string): WipProxyRouter {
  console.warn(
    `wipProxy: not mounted — ${reason}. /wip answers 503 until a key is configured.`
  )
  const router = Router()
  const unavailable = (_req: Request, res: Response) => answerUnavailable(res, reason)
  router.get('/files/:fileId/content', unavailable)
  for (const prefix of WIP_API_PREFIXES) {
    router.all(prefixPattern(prefix), unavailable)
  }
  return Object.assign(router, { unavailableReason: reason })
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
 * startup, like the MCP server's `WIP_API_KEY_FILE`) or `apiKey`.
 *
 * An `apiKeyFile` that is missing, unreadable, or empty falls back to
 * `apiKey` when one is set — with a startup warning, because the file is
 * the rotation-aware source and running on the inline key means a rotation
 * won't apply until the path resolves. The same app config can therefore
 * serve host-run dev (file exists on the host) and containerized dev (a
 * bind-mounted .env carries a host path that doesn't exist in-container,
 * but the deployer injects the inline key) without crashing either way.
 *
 * Throws only when NO source yields a non-empty key, naming every attempt —
 * a misconfigured proxy still fails loudly at construction rather than
 * silently 401-ing every upstream call.
 */
export function resolveApiKey(options: WipProxyOptions): string {
  let fileFailure: string | null = null
  if (options.apiKeyFile) {
    let key: string | null = null
    try {
      key = readFileSync(options.apiKeyFile, 'utf8').trim()
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err)
      fileFailure = `apiKeyFile '${options.apiKeyFile}' is unreadable (${reason})`
    }
    if (key) return checkedKey(key, `apiKeyFile '${options.apiKeyFile}'`)
    if (key !== null) fileFailure = `apiKeyFile '${options.apiKeyFile}' is empty`
  }
  // Trimmed like the file: a key from an environment variable often carries a
  // trailing newline (a Secret written from `jq -r … > file`), which Node
  // refuses in a header value, so every proxied call would fail.
  const inline = options.apiKey?.trim()
  if (inline) {
    if (fileFailure) {
      console.warn(
        `wipProxy: ${fileFailure} — falling back to the inline apiKey. ` +
        'Key rotation via the file will not apply until the path resolves.'
      )
    }
    return checkedKey(inline, 'apiKey')
  }
  if (fileFailure) {
    throw new Error(`wipProxy: ${fileFailure} and no inline apiKey is set`)
  }
  throw new Error('wipProxy: one of apiKey or apiKeyFile is required')
}

/**
 * A trimmed key that still holds whitespace or a control character is not a
 * WIP key (those are URL-safe base64 or hex): refuse it here, at
 * construction, rather than on the first request as ERR_INVALID_CHAR or 401.
 */
function isCleanKey(key: string): boolean {
  // eslint-disable-next-line no-control-regex
  return !/[\s\u0000-\u001f\u007f]/.test(key)
}

function checkedKey(key: string, source: string): string {
  if (!isCleanKey(key)) {
    throw new Error(
      `wipProxy: ${source} contains whitespace or a control character; WIP keys never do`,
    )
  }
  return key
}

export { WIP_API_PREFIXES } from './api-proxy.js'
export { appConfigHandler } from './app-config.js'
export type { AppConfigEntries } from './app-config.js'
export type { WipProxyOptions as WipProxyConfig }
export type { ApiProxyOptions, FileProxyOptions }
