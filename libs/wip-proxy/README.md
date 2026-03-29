# @wip/proxy

Express middleware for proxying WIP API calls with auth injection and file content streaming.

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
- **File proxy:** `GET /files/:fileId/content` proxies file downloads (resolves MinIO URLs server-side)
- **Raw body forwarding:** Uses `express.raw()` to avoid JSON parsing — request bodies are forwarded unchanged
- **Header forwarding:** Propagates `content-type`, `content-disposition`, `content-length` from upstream
- **Error handling:** Upstream failures return 502 with structured error JSON

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
| `apiKey` | `string` | required | API key for upstream requests |
| `bodyLimit` | `string` | `'100mb'` | Max request body size |
| `extraHeaders` | `Record<string, string>` | `{}` | Additional headers forwarded upstream |

## Frontend Configuration

When using `@wip/client` through the proxy:

```typescript
import { createWipClient } from '@wip/client'

const wip = createWipClient({
  baseUrl: '/wip',          // or '' if proxy is mounted at root
  auth: { type: 'none' },   // proxy handles auth
})
```
