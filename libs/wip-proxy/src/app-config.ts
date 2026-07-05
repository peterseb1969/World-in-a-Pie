import type { Request, Response } from 'express'

/**
 * Values served by the runtime app-config endpoint. `undefined` entries are
 * dropped from the response (so `process.env.X` reads can be passed directly);
 * `null` is served as an explicit "not configured".
 */
export type AppConfigEntries = Record<string, string | number | boolean | null | undefined>

/**
 * Runtime app-config endpoint (CASE-551).
 *
 * Single source of truth for client-visible per-deployment values (namespace
 * names, feature flags, display labels). The SPA fetches this at boot instead
 * of baking `VITE_*` values into the bundle at build time — a build-time bake
 * and the server's runtime env are two independent sources that drift
 * silently (the CASE-551 footgun). `VITE_BASE_PATH` stays a build-time bake
 * by design: it is consumed at bundle-emit time and fails loudly when wrong.
 *
 * Mount at the contract path `GET <base>/api/app-config`:
 * ```ts
 * router.get('/api/app-config', appConfigHandler({
 *   namespace: process.env.WIP_NAMESPACE || null,
 * }))
 * ```
 *
 * The response is an allowlist BY CONSTRUCTION: exactly the entries passed
 * here, nothing else. Never spread `process.env` into it — the same env holds
 * `WIP_API_KEY` — and never include secrets: this JSON is served to every
 * browser that can reach the app.
 *
 * Pass a function to re-read values per request (e.g. after a runtime
 * config rotation); a plain object is captured once at mount.
 */
export function appConfigHandler(
  entries: AppConfigEntries | (() => AppConfigEntries),
): (req: Request, res: Response) => void {
  return (_req: Request, res: Response) => {
    const source = typeof entries === 'function' ? entries() : entries
    const config: Record<string, string | number | boolean | null> = {}
    for (const [key, value] of Object.entries(source)) {
      if (value !== undefined) config[key] = value
    }
    // Deployment config can change on redeploy — never let a CDN/browser pin it.
    res.set('Cache-Control', 'no-store')
    res.json(config)
  }
}
