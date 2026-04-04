# Migration Guide: Legacy Def-Store Validation API Removed

**Date:** 2026-04-04
**Commit:** 3dece58 (develop)
**Affects:** Any app calling def-store's `/validation/validate` or `/validation/validate-bulk`

## What Changed

The legacy validation endpoints on the def-store service have been removed:

| Removed Endpoint | Replacement |
|-----------------|-------------|
| `POST /api/def-store/validation/validate` | `POST /api/def-store/validate` |
| `POST /api/def-store/validation/validate-bulk` | `POST /api/def-store/validate/bulk` |

The request/response schemas are identical — only the URL path changed.

## Why

The legacy endpoints had three problems:

1. **No synonym resolution.** If you passed a terminology synonym (e.g., `"STATUS"`) as `terminology_id`, it was sent directly to the service without resolving to the canonical ID. This could silently fail or return incorrect results.
2. **No authentication.** The legacy endpoints did not require `X-API-Key` — they were unprotected.
3. **Duplicated code.** The same functionality existed in a better form on the terms router, with resolution and auth.

## How to Audit Your App

Search your codebase for references to the old paths:

```bash
# In your app directory:
grep -rn "validation/validate" src/ lib/ --include="*.ts" --include="*.js" --include="*.py" --include="*.vue"
```

You're looking for any HTTP call that hits the **def-store** service at these paths:
- `/validation/validate`
- `/validation/validate-bulk`
- `/api/def-store/validation/validate`
- `/api/def-store/validation/validate-bulk`

**Important:** The **document-store** also has a `/validation/validate` endpoint — that one is NOT removed. Only the def-store paths are affected. Check which service the call targets:
- If the base URL points to port 8002 or includes `def-store` → **affected, must migrate**
- If the base URL points to port 8004 or includes `document-store` → **not affected**

## How to Fix

### If using `@wip/client` (TypeScript)

The `@wip/client` library does **not** expose the def-store validation endpoints directly. If you're calling them via raw HTTP, switch to the MCP tools:

```typescript
// Before: raw HTTP to def-store
const resp = await fetch(`${DEF_STORE_URL}/api/def-store/validation/validate`, {
  method: 'POST',
  body: JSON.stringify({ terminology_id: 'STATUS', value: 'approved' }),
});

// After: use the MCP validate_term_value tool, or call the new path
const resp = await fetch(`${DEF_STORE_URL}/api/def-store/validate`, {
  method: 'POST',
  headers: { 'X-API-Key': apiKey },  // Now required!
  body: JSON.stringify({ terminology_id: 'STATUS', value: 'approved' }),
});
```

### If using raw HTTP (Python, curl, etc.)

Change the URL path and add the API key header:

```python
# Before
response = httpx.post(
    f"{def_store_url}/api/def-store/validation/validate",
    json={"terminology_id": "STATUS", "value": "approved"},
)

# After
response = httpx.post(
    f"{def_store_url}/api/def-store/validate",
    json={"terminology_id": "STATUS", "value": "approved"},
    headers={"X-API-Key": api_key},  # Now required!
)
```

For bulk validation:

```python
# Before
response = httpx.post(
    f"{def_store_url}/api/def-store/validation/validate-bulk",
    json={"items": [...]},
)

# After
response = httpx.post(
    f"{def_store_url}/api/def-store/validate/bulk",   # Note: /bulk not -bulk
    json={"items": [...]},
    headers={"X-API-Key": api_key},
)
```

### Key differences in the new endpoints

1. **Authentication required.** All calls must include `X-API-Key` header.
2. **Synonym resolution works.** You can pass terminology synonyms (e.g., `"STATUS"`) as `terminology_id` and they will be resolved to canonical IDs automatically.
3. **Path change.** `/validation/validate-bulk` → `/validate/bulk` (slash, not hyphen).

### If using MCP tools

If you validate terms via the `validate_term_value` MCP tool, no changes are needed — the MCP server has been updated internally.

## Verification

After migrating, verify your calls work:

```bash
# Single validation
curl -X POST http://localhost:8002/api/def-store/validate \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"terminology_id": "STATUS", "value": "approved"}'

# Bulk validation
curl -X POST http://localhost:8002/api/def-store/validate/bulk \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"items": [{"terminology_id": "STATUS", "value": "approved"}]}'
```

Both should return 200 with validation results. If you get 404, you're still hitting the old path. If you get 401/403, check your API key.
