# API Key Management

WIP supports two kinds of API keys: **config keys** loaded from a JSON file at startup, and **runtime keys** created and managed through a REST API. This guide covers runtime key management — creating, listing, updating, and revoking keys without restarting services.

---

## Overview

| Aspect | Config Keys | Runtime Keys |
|--------|-------------|--------------|
| Defined in | `config/api-keys.json` | MongoDB (via REST API) |
| Created by | Editing a file + restarting services | `POST /api/registry/api-keys` |
| Modifiable via API | No (read-only) | Yes |
| Deletable via API | No | Yes |
| Use case | Bootstrap keys (admin, service accounts) | App keys, temporary keys, automated provisioning |

Both types appear in the key list and work identically for authentication. The `source` field (`"config"` or `"runtime"`) tells you which kind a key is.

---

## Creating a Key

```bash
curl -k -X POST https://localhost:8443/api/registry/api-keys \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-app",
    "owner": "peter",
    "groups": [],
    "namespaces": ["production"],
    "description": "Production key for my-app"
  }'
```

Response (HTTP 201):

```json
{
  "name": "my-app",
  "owner": "peter",
  "groups": [],
  "namespaces": ["production"],
  "description": "Production key for my-app",
  "source": "runtime",
  "enabled": true,
  "created_at": "2026-04-06T17:30:00Z",
  "expires_at": null,
  "created_by": "apikey:admin-console",
  "plaintext_key": "wip_a1b2c3d4e5f6..."
}
```

**Save the `plaintext_key` immediately.** It is shown once and never returned again. WIP stores only the bcrypt hash.

### Required fields

- **`name`** — unique identifier for the key. Cannot collide with a config-file key name.

### Optional fields

| Field | Default | Description |
|-------|---------|-------------|
| `owner` | `"system"` | Who owns this key (freeform, for your records) |
| `groups` | `[]` | Authorization groups (e.g., `["wip-admins"]`) |
| `namespaces` | `null` | Namespace scope. Non-privileged keys **must** have this set — keys without namespace scope get no access (403 on all requests). |
| `description` | `null` | Human-readable purpose |
| `expires_at` | `null` | ISO 8601 expiration datetime. `null` = never expires. |

### Namespace scoping rules

- **Privileged groups** (`wip-admins`, `wip-services`): namespace scope is optional — these keys have access to all namespaces automatically.
- **All other keys**: must declare `namespaces` explicitly. A key scoped to `["production"]` can only access data in the `production` namespace.
- **Single-namespace keys** get a convenience feature: if the caller omits the `namespace` query parameter, the server derives it from the key's scope. Multi-namespace keys must always pass `namespace` explicitly.

---

## Listing Keys

```bash
curl -k https://localhost:8443/api/registry/api-keys \
  -H "X-API-Key: <admin-key>"
```

Returns all keys (config + runtime) with metadata. No hashes or plaintext are ever included:

```json
[
  {
    "name": "admin-console",
    "owner": "admin@wip.local",
    "groups": ["wip-admins"],
    "namespaces": null,
    "source": "config",
    "enabled": true,
    ...
  },
  {
    "name": "my-app",
    "owner": "peter",
    "groups": [],
    "namespaces": ["production"],
    "source": "runtime",
    "enabled": true,
    ...
  }
]
```

## Getting a Single Key

```bash
curl -k https://localhost:8443/api/registry/api-keys/my-app \
  -H "X-API-Key: <admin-key>"
```

---

## Updating a Key

Update metadata on a runtime key. Config keys cannot be modified via API.

```bash
curl -k -X PATCH https://localhost:8443/api/registry/api-keys/my-app \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "groups": ["wip-users"],
    "description": "Updated description",
    "enabled": false
  }'
```

All fields are optional — only include what you want to change:

| Field | Description |
|-------|-------------|
| `description` | Update the description |
| `groups` | Replace the groups list |
| `namespaces` | Replace the namespace scope |
| `expires_at` | Set or clear expiration |
| `enabled` | `false` to disable (key stops working), `true` to re-enable |

Disabling a key (`"enabled": false`) is an alternative to deletion when you want to temporarily suspend access. Disabled keys are excluded from the sync endpoint, so other services stop accepting them within ~30 seconds.

---

## Revoking (Deleting) a Key

```bash
curl -k -X DELETE https://localhost:8443/api/registry/api-keys/my-app \
  -H "X-API-Key: <admin-key>"
```

Response:

```json
{
  "status": "deleted",
  "name": "my-app"
}
```

The key is **permanently removed** from MongoDB and immediately invalidated on the Registry service. Other services stop accepting it after their next sync cycle (~30 seconds).

Config-file keys cannot be deleted via the API — you'll get a 400 error.

---

## How Key Propagation Works

When you create or revoke a runtime key, the change takes effect on the Registry immediately. Other services (def-store, template-store, document-store) discover changes through **background polling**:

1. Each service polls the Registry's `/api/registry/api-keys/sync` endpoint every 30 seconds.
2. The sync endpoint returns all enabled runtime keys (with hashes, never plaintext).
3. The service atomically replaces its local runtime key set.
4. Config-file keys are never affected by sync — they remain as loaded at startup.

**Timing:** After creating a key, it works on the Registry instantly. Allow up to 30 seconds for other services to pick it up. After revoking, the same delay applies — the key may still work on non-Registry services for up to 30 seconds.

---

## MCP Tools

If you're using WIP through an AI agent (Claude Code, Claude Desktop), three MCP tools are available:

| Tool | Description |
|------|-------------|
| `create_api_key` | Create a key. Returns the plaintext once. |
| `list_api_keys` | List all keys with metadata. |
| `revoke_api_key` | Delete a runtime key by name. |

Example via MCP:

```
create_api_key(name="my-app", namespaces=["production"], owner="peter")
```

---

## TypeScript Client (`@wip/client`)

```typescript
import { createWipClient } from '@wip/client'

const wip = createWipClient({ baseUrl: '...', auth: { ... } })

// Create
const { plaintext_key } = await wip.registry.createAPIKey({
  name: 'my-app',
  namespaces: ['production'],
  owner: 'peter',
})

// List
const keys = await wip.registry.listAPIKeys()

// Get
const key = await wip.registry.getAPIKey('my-app')

// Update
await wip.registry.updateAPIKey('my-app', {
  groups: ['wip-users'],
  enabled: false,
})

// Revoke
await wip.registry.revokeAPIKey('my-app')
```

---

## Error Responses

| Status | Cause |
|--------|-------|
| 201 | Key created successfully |
| 200 | List/get/update/delete succeeded |
| 400 | Attempted to modify or delete a config-file key |
| 401 | Missing or invalid API key |
| 403 | Caller lacks admin permission |
| 404 | Key not found |
| 409 | Name already exists (runtime or config key) |
| 422 | Invalid request body (extra fields, wrong types) |

---

## Key Rotation

To rotate a runtime key without downtime:

1. Create a new key with the desired scope
2. Update your application to use the new key
3. Verify the new key works
4. Revoke the old key

For config-file key rotation, see `docs/security/key-rotation.md`.

---

## Security Notes

- **Plaintext is shown once.** Store it securely (environment variable, secrets manager). If lost, revoke and create a new key.
- **Hashes only in storage.** MongoDB stores bcrypt hashes. The sync endpoint exposes hashes to other services for verification — never plaintext.
- **Admin-only management.** All management endpoints require an admin API key or admin JWT token.
- **Extra fields are rejected.** The API returns 422 if you include unknown fields in create or update requests.
