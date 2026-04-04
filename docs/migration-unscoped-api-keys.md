# Migration Guide: Unscoped API Keys No Longer Have Access

**Date:** 2026-04-04
**Affects:** Any app using an API key without an explicit `namespaces` field and not in a privileged group (`wip-admins` or `wip-services`)

## What Changed

API keys with `namespaces: null` (or no `namespaces` field) that are **not** in a privileged group now receive **no access** to any namespace. Previously, these keys had unrestricted access — they could reach any namespace through group grants or fallback behavior.

**Privileged groups are exempt:** Keys in `wip-admins` or `wip-services` continue to work without namespace scoping. This is by design — admin and service keys need all-namespace access.

## Why

Unscoped keys were a security gap. A non-admin key without explicit namespace restrictions could access any namespace it had group grants on, with no ceiling. When new namespaces were created and group grants added, unscoped keys silently gained access. This violated namespace isolation.

## Am I Affected?

**You are affected if** your app uses an API key that:
1. Has **no `namespaces` field** in its key config, AND
2. Is **not** in the `wip-admins` or `wip-services` group

**You are NOT affected if:**
- Your key is in `wip-admins` or `wip-services` group (admin/service keys)
- Your key already has an explicit `namespaces` list
- You use the dev master key (`dev_master_key_for_testing`) — it has `wip-admins` group

### How to check

Look at your API key configuration. If you use a file-based key:

```bash
# Check your api-keys JSON config
cat config/api-keys.*.json | python3 -m json.tool
```

For each key, check:
- Does it have a `"namespaces"` field? If not, it's unscoped.
- Does it have `"wip-admins"` or `"wip-services"` in its `"groups"`? If not, it's non-privileged.

If your key is unscoped AND non-privileged, it will now get 403 on every request.

**Startup warning:** The server now logs a warning at startup for misconfigured keys:
```
WARNING wip_auth: API key 'my-app' has no namespace scope and is not in a privileged group — it will have NO access.
```

Check your service logs after restarting.

## How to Fix

Add a `namespaces` field to your key config listing the namespaces your app needs:

```json
{
  "name": "my-app",
  "key": "my_secret_key",
  "owner": "my-app@example.com",
  "groups": [],
  "namespaces": ["production"],
  "description": "My app — scoped to production namespace"
}
```

If your app needs multiple namespaces:

```json
{
  "namespaces": ["production", "staging"]
}
```

### What namespace scoping gives you

A scoped key with one namespace gets two benefits:

1. **Implicit namespace derivation.** When you omit the `namespace` query parameter, the server derives it from your key's single namespace. This means synonym resolution works without passing `namespace` on every request.

2. **Read access fallback.** If your key has no explicit grant on its namespace but the namespace is in the key's list, you get `read` access by default. Explicit grants can upgrade this to `write` or `admin`.

### For apps using `create-app-project.sh`

The scaffold currently generates apps using the dev master key. Replace it with a namespace-scoped key in your app's `.env`:

```bash
# In your app's .env:
WIP_API_KEY=your_namespace_scoped_key
```

Until runtime API key management is available, generate keys with `scripts/security/generate-api-key.sh` and add them to the API keys config file.

## Verification

After updating your key config, restart the WIP services and verify:

```bash
# Check your app can access its namespace
curl -s http://localhost:8002/api/def-store/terminologies?namespace=YOUR_NS \
  -H "X-API-Key: YOUR_KEY" | head -c 200

# Should return 200 with data. If you get 403, check:
# 1. Your key has the right namespace in its list
# 2. The namespace exists
# 3. The service was restarted after config change
```

## Reference: Privileged Groups

| Group | Purpose | Namespace scoping |
|-------|---------|-------------------|
| `wip-admins` | Human admins, dev keys | Not required — gets admin on all namespaces |
| `wip-services` | Service-to-service (reporting-sync, etc.) | Not required — can access all namespaces |
| Any other group | App keys, ETL pipelines, dashboards | **Required** — must have explicit `namespaces` |
