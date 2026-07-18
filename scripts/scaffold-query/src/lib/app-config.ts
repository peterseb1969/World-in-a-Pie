/**
 * Runtime app config (CASE-551).
 *
 * Client-visible per-deployment values (the WIP namespace, feature flags, …)
 * come from the server at runtime — `GET <base>/api/app-config` — NOT from
 * `VITE_*` build-time bakes. A bake and the server's runtime env are two
 * independent sources that drift silently: the server reads `WIP_NAMESPACE`
 * at runtime while a baked client constant keeps whatever the build saw.
 * (`VITE_BASE_PATH` is the deliberate exception: consumed at bundle-emit
 * time, and loud when wrong.)
 *
 * Usage — fetch once, share the promise:
 * ```ts
 * import { fetchAppConfig } from './lib/app-config'
 * const config = await fetchAppConfig()   // { namespace: string | null, ... }
 * ```
 * The result is cached module-level; every caller awaits the same request.
 * If your render gates on a config value, await this before first render
 * (e.g. in main.tsx, or a suspense/bootstrap gate) rather than defaulting —
 * a wrong default is exactly the silent drift this exists to prevent.
 */

export interface AppConfig {
  /** The deployment's WIP namespace; null when the server has none configured. */
  namespace: string | null
  [key: string]: unknown
}

const BASE = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')

let cached: Promise<AppConfig> | null = null

export function fetchAppConfig(): Promise<AppConfig> {
  if (!cached) {
    cached = fetch(`${BASE}/api/app-config`).then((res) => {
      if (!res.ok) {
        // Fail loud: a missing config endpoint means the server contract is
        // broken; silently defaulting would reintroduce the drift footgun.
        cached = null
        throw new Error(`app-config fetch failed: HTTP ${res.status}`)
      }
      return res.json() as Promise<AppConfig>
    })
  }
  return cached
}
