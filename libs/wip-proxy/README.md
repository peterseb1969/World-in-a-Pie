# @wip/proxy

Express middleware for proxying WIP API calls with auth injection and file content streaming.

`express` is a peer dependency: both Express 4 (`^4.17.0`) and Express 5 are supported — the test suite runs against a real install of each major.

## Usage

```typescript
import express from 'express'
import { wipProxy } from '@wip/proxy'

const app = express()

// Mount at root — frontend calls /api/{service}/* directly
app.use(wipProxy({
  baseUrl: process.env.WIP_BASE_URL || 'https://localhost:8443',
  apiKey: process.env.WIP_API_KEY,
}))

// Or mount at /wip — frontend uses @wip/client with baseUrl: '/wip'
const proxy = wipProxy({
  baseUrl: process.env.WIP_BASE_URL || 'https://localhost:8443',
  apiKey: process.env.WIP_API_KEY,          // set by wip-deploy
  apiKeyFile: process.env.WIP_API_KEY_FILE, // the app's own key file, when run outside it
  onMissingKey: 'unavailable',
})
app.use('/wip', proxy)
```

### A key that arrives or rotates while the app runs

`apiKey` and `apiKeyFile` are read once, when `wipProxy()` is called. For a
key that appears later (typed into a settings page) or rotates in place (a
Secret mounted as a file, which the kubelet refreshes), give a provider
instead. It is asked on every proxied request:

```typescript
import { wipProxy, keyFromFile } from '@wip/proxy'

// A mounted Secret: re-read at most every 15 s (ttlMs), no restart needed
app.use('/wip', wipProxy({ baseUrl, getApiKey: keyFromFile('/var/run/wip/api-key') }))

// A key the app holds itself (e.g. saved from its settings page)
app.use('/wip', wipProxy({ baseUrl, getApiKey: () => settings.wipApiKey }))
```

- `getApiKey` is then the **only** source — `apiKey` / `apiKeyFile` alongside
  it are ignored, with one warning at construction, and never used as a
  fallback. `onMissingKey` does not apply.
- Its result is trimmed and checked like a static key. No key, a key holding
  whitespace or a control character, or a provider that throws answers that
  request **503** `wip_proxy_unavailable` naming the source — never the value
  — and the next request asks again, so the proxy recovers as soon as a key
  exists. Throw a `KeySourceError` from your own provider to have its message
  (which must name the source, not the key) shown in the 503.
- `unavailableReason` then reports the most recent request's outcome; read it
  per request (`appConfigHandler(() => ({ … }))`).
- A key held only in memory reaches only the replica that holds it; across
  replicas, use a file on a shared volume or a Secret.

### A missing key costs `/wip`, not the app

`wipProxy()` is usually called while the server module loads. With the
default `onMissingKey: 'throw'`, a configuration that yields no key stops the
process before `listen()`, and the app's health endpoint goes with it.
With `onMissingKey: 'unavailable'` the proxy's routes answer **503**
`{"error": <reason>, "error_code": "wip_proxy_unavailable"}` instead, one
warning is logged at startup, and the rest of the app serves normally. The
reason is the exact message the throw would carry: which file failed, and
that no inline key was set.

The returned router carries the same reason, so the app can show it in its
runtime config:

```typescript
router.get('/api/app-config', appConfigHandler(() => ({
  namespace: process.env.WIP_NAMESPACE ?? null,
  wip_proxy: proxy.unavailableReason ? 'unavailable' : 'available',
})))
```

Pass an unset source as `undefined` (as `process.env.X` already is), not
`process.env.X || ''`: an empty string is no key, so `apiKey: process.env.WIP_API_KEY || ''`
looks like a fallback and gives none.

## What It Does

- **API proxy:** `/api/{service}/*`, every method, forwarded to WIP with API key injected. The path is checked after normalisation: dot segments (`..`, `%2e%2e`, backslashes) that would carry a request outside the six service prefixes — or into a look-alike prefix — are refused with `400` `wip_proxy_path_refused` and never sent
- **File proxy:** `GET /files/:fileId/content` proxies file downloads (resolves object-storage URLs server-side, following redirects so internal storage URLs never reach the browser). The key goes only to WIP's own origin — a hop to another origin is made without it — and a redirect with an unparseable `Location` answers `502`. The file id is one path segment
- **Streaming both directions:** request bodies pipe into the upstream and upstream responses pipe back out, with backpressure — proxy memory is O(1) in payload size, so multi-GB archive uploads/downloads pass through flat. Built on `node:http` deliberately: a `fetch`-based streamed request body still buffers whole inside undici (its backpressure never reaches the source socket)
- **No client identity upstream:** a request reaches WIP with the app's key and only `content-type` / `content-length` from the client (plus `extraHeaders`). A client-sent `X-WIP-User` / `X-WIP-Groups` is never forwarded — the proxy cannot tell who set it, and under a privileged key WIP would act on it as the logged-in user. WIP sees every request as the app's key
- **Header forwarding:** upstream response headers are an allowlist: `content-type`, `content-disposition`, `content-length` (and a rewritten `Location`, below)
- **Error handling:** WIP's own responses — including 4xx/5xx — are forwarded with their status and body as they are, and the allowlisted headers. Proxy-level failures to reach WIP return `502` with structured error JSON when nothing has been sent yet; a failure mid-stream destroys the connection (a truncated transfer is the honest signal once bytes have flowed)
- **Redirects:** API-route redirects are forwarded to the client, not followed — a streamed request body cannot be replayed on a 307. A `Location` onto a WIP API path is rewritten under the proxy's mount (`/api/registry/` → `/wip/api/registry/`), so the client follows it through the proxy; any other `Location` is dropped

### Mount order matters

The proxy consumes the request as a raw stream. Mount it **before** any
body-parsing middleware (`express.json()`, `express.raw()`, …) that would
match the same paths — a parser ahead of the proxy drains the body, and the
upstream request, told the body's length, waits for bytes that never come:
the call stalls until a timeout.

```typescript
app.use('/wip', wipProxy({ ... }))  // proxy first
app.use(express.json())             // parsers after, or scoped to app routes
```

## Proxied Services

- `/api/registry/*`
- `/api/def-store/*`
- `/api/template-store/*`
- `/api/document-store/*`
- `/api/reporting-sync/*`
- `/api/ingest-gateway/*`

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `baseUrl` | `string` | required | WIP instance URL |
| `apiKey` | `string` | — | API key for upstream requests (provide this, `apiKeyFile` or `getApiKey`) |
| `apiKeyFile` | `string` | — | Path to a file containing the app's own API key (e.g. `WIP_API_KEY_FILE`); read once at construction; takes precedence over `apiKey` |
| `getApiKey` | `() => string \| null \| undefined \| Promise<…>` | — | Key provider asked per request; when set, the only source (see *A key that arrives or rotates while the app runs*). `keyFromFile(path, { ttlMs })` builds one that re-reads a file |
| `bodyLimit` | `string` | — | **Deprecated, no effect since 0.5.0** — bodies stream instead of buffering, so there is no buffer for a limit to protect. Accepted for config compatibility |
| `extraHeaders` | `Record<string, string>` | `{}` | Additional headers forwarded upstream |
| `defaultNamespace` | `string` | — | Namespace appended to `POST /api/document-store/documents/query` (only) when the caller hasn't scoped it — under a key spanning several namespaces, which every app key does, a value-form `template_id` is otherwise refused with 422 |
| `onMissingKey` | `'throw' \| 'unavailable'` | `'throw'` | When no source yields a key: throw at construction, or answer 503 on the proxy's routes (see *A missing key costs `/wip`, not the app*) |

## Other Exports

Beyond `wipProxy`, the package exports these building blocks:

### `keyFromFile(path, { ttlMs })`

A `getApiKey` provider reading `path`, re-read at most once per `ttlMs`
(default 15 000). A missing, unreadable or empty file is "no key now": the
503 names the path, and the next read after `ttlMs` tries again.

### `appConfigHandler(entries)`

Express handler serving deploy-time values to the browser as JSON (the
runtime app-config pattern — client-visible values fetched at runtime, not
baked into the build). Takes an `AppConfigEntries` object, or a function
returning one to re-read values per request; responds with
`Cache-Control: no-store` so a redeploy's new values are never pinned by a
CDN or browser.

**The response is an allowlist by construction** — exactly the entries you
pass, nothing else. Never spread `process.env` into it (the same env holds
`WIP_API_KEY`) and never include secrets: this JSON is served to every
browser that can reach the app.

```typescript
app.get('/app-config', appConfigHandler({
  wipNamespace: process.env.WIP_NAMESPACE ?? null,
  appVersion: process.env.APP_VERSION ?? null,
}))
```

### `resolveApiKey(options)`

The key-resolution used by `wipProxy` itself, exported for apps that need
the upstream key outside the proxy (e.g. a server-side script sharing the
proxy's config). Prefers `apiKeyFile` (read once at startup — the
rotation-aware source); a missing/unreadable/empty file falls back to the
inline `apiKey` with a startup warning. Throws, naming every attempted
source, when nothing yields a key — a misconfigured proxy fails loudly at
construction instead of silently 401-ing every upstream call.

### `WIP_API_PREFIXES`

The list of `/api/<service>` route prefixes the proxy forwards (the same
six listed under *Proxied Services*). Useful for mounting your own
middleware around exactly the proxied surface.

### `prefixPattern(prefix)`

Anchored `RegExp` for a service prefix and everything under it —
`/api/def-store` and `/api/def-store/terminologies` match,
`/api/def-store-evil` does not. Exported for route-matching tests.

## Frontend Configuration

When using `@wip/client` through the proxy:

```typescript
import { createWipClient } from '@wip/client'

const wip = createWipClient({
  baseUrl: '/wip',          // or '' if proxy is mounted at root
  // no `auth` — the proxy injects credentials server-side. (`auth` is
  // optional; there is no `{ type: 'none' }` variant in @wip/client.)
})
```
