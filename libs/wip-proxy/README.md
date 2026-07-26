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
app.use('/wip', wipProxy({
  baseUrl: process.env.WIP_BASE_URL || 'https://localhost:8443',
  apiKey: process.env.WIP_API_KEY,
}))
```

## What It Does

- **API proxy:** `GET|POST|PUT|DELETE /api/{service}/*` forwarded to WIP with API key injected
- **File proxy:** `GET /files/:fileId/content` proxies file downloads (resolves MinIO URLs server-side, following redirects so internal storage URLs never reach the browser)
- **Streaming both directions:** request bodies pipe into the upstream and upstream responses pipe back out, with backpressure — proxy memory is O(1) in payload size, so multi-GB archive uploads/downloads pass through flat. Built on `node:http` deliberately: a `fetch`-based streamed request body still buffers whole inside undici (its backpressure never reaches the source socket)
- **Header forwarding:** Propagates `content-type`, `content-disposition`, `content-length` from upstream
- **Error handling:** WIP's own responses — including 4xx/5xx — are forwarded verbatim (status, headers, body). Proxy-level failures to reach WIP return `502` with structured error JSON when nothing has been sent yet; a failure mid-stream destroys the connection (a truncated transfer is the honest signal once bytes have flowed). API-route redirects are forwarded to the client, not followed — a streamed request body cannot be replayed on a 307

### Mount order matters

The proxy consumes the request as a raw stream. Mount it **before** any
body-parsing middleware (`express.json()`, `express.raw()`, …) that would
match the same paths — a parser ahead of the proxy drains the body, and the
proxy then forwards an empty stream:

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
| `apiKey` | `string` | — | API key for upstream requests (provide this or `apiKeyFile`) |
| `apiKeyFile` | `string` | — | Path to a file containing the API key (e.g. the live wip-deploy secrets file); read once at construction; takes precedence over `apiKey` |
| `bodyLimit` | `string` | — | **Deprecated, no effect since 0.5.0** — bodies stream instead of buffering, so there is no buffer for a limit to protect. Accepted for config compatibility |
| `extraHeaders` | `Record<string, string>` | `{}` | Additional headers forwarded upstream |
| `forwardIdentity` | `boolean` | `false` | Forward `X-WIP-User`, `X-WIP-Groups`, `X-WIP-Auth-Method` from the incoming request |
| `defaultNamespace` | `string` | — | Namespace appended to the documents query endpoint when the caller hasn't scoped it (guards the multi-namespace-key silent-zero-rows trap) |

## Other Exports

Beyond `wipProxy`, the package exports four building blocks:

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
