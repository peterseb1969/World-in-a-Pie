# Namespace Authorization Design

**Status:** Planning
**Dependency for:** Natural Language Interface, multi-user deployments

## Problem

WIP authentication is binary: authenticated = full access to all namespaces. This is fine for single-user deployments but breaks the moment access is shared. The Natural Language Interface makes this urgent — giving a friend access to your D&D namespace shouldn't let them query your payslips.

## Current State

What exists today:

| Layer | Status |
|-------|--------|
| Authentication (who are you?) | Working — Dex OIDC + API keys, dual mode |
| Identity model | Working — `UserIdentity` with groups, `APIKeyRecord` with namespaces field |
| Namespace isolation | Partial — `check_namespace_access()` exists in wip-auth but is never called |
| Group enforcement | Minimal — only `require_admin()` on setup endpoints |
| Dex group assignment | Missing — static users have no groups in Dex config |
| Per-namespace permissions | Missing — no mapping from user to namespace to permission level |

The foundation is solid. The gaps are in enforcement, not architecture.

## Design Principles

1. **Backwards compatible.** Existing single-user deployments work unchanged — default is "owner has full access to everything."
2. **Namespace-scoped.** Permissions are per-namespace, not global. A user can be admin on one namespace and have no access to another.
3. **Enforced in wip-auth.** Not in each service. Services call one dependency; the library does the checking.
4. **API keys and OIDC users use the same model.** A JWT user and an API key get the same permission checks.
5. **Opt-in complexity.** Single-user mode needs zero configuration. Multi-user mode requires explicit grants.

## Permission Model

```
User/APIKey  →  NamespaceGrant  →  Namespace
                 (permission level)
```

### Permission Levels

| Level | Can do | Use case |
|-------|--------|----------|
| `none` | Namespace is invisible | Default for ungrouped users |
| `read` | List, get, query documents and metadata | Friends, viewers, NLI read-only |
| `write` | Create, update documents | Collaborators, NLI with write enabled |
| `admin` | Manage templates, terminologies, grant access, delete | Namespace owners |

Permissions are hierarchical: `admin` includes `write`, `write` includes `read`.

### Grant Storage

Grants are stored in the Registry service (which already owns namespaces):

```python
# New collection: namespace_grants
{
    "namespace": "dnd-campaign",
    "subject": "friend@example.com",     # Email (OIDC) or API key name
    "subject_type": "user",              # "user" | "api_key" | "group"
    "permission": "read",                # "read" | "write" | "admin"
    "granted_by": "peter@wip.local",
    "granted_at": "2026-03-18T...",
    "expires_at": null                   # Optional expiration
}
```

Group-based grants allow bulk assignment:

```python
# Grant all wip-editors write access to a namespace
{
    "namespace": "shared-finance",
    "subject": "wip-editors",
    "subject_type": "group",
    "permission": "write",
    ...
}
```

### Resolution Order

When checking permissions for a user on a namespace:

1. **Superadmin check** — users in `wip-admins` group have `admin` on all namespaces (preserves current behavior)
2. **Direct user grant** — explicit grant for this user on this namespace
3. **Group grant** — grant for any of the user's groups on this namespace
4. **API key namespace list** — `APIKeyRecord.namespaces` field. **Enforced (2026-04-04):** non-privileged keys without `namespaces` get `none` permission. Only `wip-admins` and `wip-services` can omit this field.
5. **Default** — `none` (namespace invisible)

If multiple grants match, the highest permission wins.

## API Endpoints

New endpoints on the Registry service:

```
# Namespace grant management (requires admin on the namespace)
GET    /api/registry/namespaces/{prefix}/grants          # List grants
POST   /api/registry/namespaces/{prefix}/grants          # Create grant(s) — bulk
DELETE /api/registry/namespaces/{prefix}/grants          # Revoke grant(s) — bulk

# User's own permissions (any authenticated user)
GET    /api/registry/my/namespaces                        # List namespaces I can access + my permission level
GET    /api/registry/my/namespaces/{prefix}/permission    # My permission on a specific namespace
```

### Grant Request

```json
[
    {
        "subject": "friend@example.com",
        "subject_type": "user",
        "permission": "read",
        "expires_at": null
    }
]
```

Follows the bulk-first convention.

## Enforcement in wip-auth

### New Dependency Functions

```python
# New dependencies added to wip-auth/dependencies.py

async def require_namespace_read(namespace: str) -> UserIdentity:
    """Require read (or higher) access to a namespace."""
    identity = await require_identity()
    await check_namespace_permission(identity, namespace, "read")
    return identity

async def require_namespace_write(namespace: str) -> UserIdentity:
    """Require write (or higher) access to a namespace."""
    identity = await require_identity()
    await check_namespace_permission(identity, namespace, "write")
    return identity

async def require_namespace_admin(namespace: str) -> UserIdentity:
    """Require admin access to a namespace."""
    identity = await require_identity()
    await check_namespace_permission(identity, namespace, "admin")
    return identity
```

### Permission Check Flow

```python
async def check_namespace_permission(
    identity: UserIdentity,
    namespace: str,
    required: str  # "read" | "write" | "admin"
) -> None:
    """
    Check if the identity has the required permission on the namespace.
    Raises HTTP 403 if not.
    Raises HTTP 404 if namespace doesn't exist (don't leak namespace names).
    """
    # Superadmin bypass
    if has_admin_group(identity):
        return

    # Check grants (cached with short TTL)
    permission = await resolve_permission(identity, namespace)

    if not permission_sufficient(permission, required):
        if permission == "none":
            raise HTTPException(404, "Namespace not found")  # Don't reveal existence
        raise HTTPException(403, f"Requires {required} access to namespace '{namespace}'")
```

Key detail: if a user has `none` permission, the namespace returns 404 (not 403) to avoid leaking namespace names.

### Grant Cache

Permission checks happen on every request. Fetching grants from the Registry on every call would be too slow. Solution:

```python
# In-process cache with short TTL
_grant_cache: dict[str, tuple[str, float]] = {}  # "user:namespace" → (permission, cached_at)
GRANT_CACHE_TTL = 30.0  # seconds

async def resolve_permission(identity: UserIdentity, namespace: str) -> str:
    cache_key = f"{identity.user_id}:{namespace}"
    now = time.monotonic()

    if cache_key in _grant_cache:
        permission, cached_at = _grant_cache[cache_key]
        if (now - cached_at) < GRANT_CACHE_TTL:
            return permission

    # Fetch from Registry
    permission = await fetch_permission_from_registry(identity, namespace)
    _grant_cache[cache_key] = (permission, now)
    return permission
```

30-second TTL means grant changes take at most 30 seconds to propagate. Acceptable for this use case.

### Service Integration

Each service adds namespace checking to its endpoints. The change is small — one additional dependency per endpoint:

```python
# Before (current)
@router.post("")
async def create_documents(
    items: list[DocumentCreateRequest] = Body(...),
    _: str = Depends(require_api_key)
):
    namespace = items[0].namespace if items else "wip"
    ...

# After
@router.post("")
async def create_documents(
    items: list[DocumentCreateRequest] = Body(...),
    identity: UserIdentity = Depends(require_identity)
):
    namespace = items[0].namespace if items else "wip"
    await check_namespace_permission(identity, namespace, "write")
    ...
```

For bulk requests with mixed namespaces (rare but possible), check permission for each unique namespace in the batch.

## Dex Configuration

Add groups to static users:

```yaml
# config/dex/config.yaml
staticPasswords:
  - email: admin@wip.local
    hash: "..."
    username: admin
    userID: "admin-uuid"
    groups:
      - wip-admins
  - email: editor@wip.local
    hash: "..."
    username: editor
    userID: "editor-uuid"
    groups:
      - wip-editors
  - email: viewer@wip.local
    hash: "..."
    username: viewer
    userID: "viewer-uuid"
    groups:
      - wip-viewers
```

The `groups` claim will appear in JWT tokens. wip-auth already reads it (via `jwt_groups_claim` config).

## Single-User Mode

For existing single-user deployments (the common case), nothing changes:

1. **API key auth** — the legacy master key is in `wip-admins` group → superadmin → full access to all namespaces
2. **OIDC admin user** — `admin@wip.local` is in `wip-admins` group → same
3. **No grants needed** — superadmin bypasses all checks

Authorization only matters when you add non-admin users or share API keys with restricted namespace lists.

## NLI Integration

The NLI service uses namespace authorization to scope the chat experience:

1. **On conversation start**, NLI calls `GET /api/registry/my/namespaces` to discover which namespaces the user can access
2. **System prompt** includes only accessible namespaces and their permission levels
3. **Tool filtering**:
   - `none` namespaces: invisible (not in system prompt, not in tool results)
   - `read` namespaces: read tools only (query, list, get, report)
   - `write` namespaces: read + write tools (create, update — with confirmation)
   - `admin` namespaces: all tools
4. **Tool execution** passes the user's identity through to WIP API calls, so the service-level checks also apply (defense in depth)

## Console Integration

The WIP Console adds namespace-aware UI:

1. **Namespace picker** filters to accessible namespaces only
2. **Read-only badge** on namespaces where user has `read` permission
3. **Settings → Namespace Access** page for admins to manage grants
4. **Invite flow**: admin enters email + namespace + permission level → creates grant

## Migration

### For Existing Deployments

No migration needed. The system defaults to:
- `wip-admins` group → superadmin (current behavior preserved)
- Legacy API key → superadmin (current behavior preserved)
- No grants → superadmin users see everything (current behavior preserved)

### For New Multi-User Deployments

```bash
# After initial setup, grant namespace access
curl -X POST http://localhost:8001/api/registry/namespaces/dnd-campaign/grants \
  -H "X-API-Key: $MASTER_KEY" \
  -d '[{"subject": "friend@example.com", "subject_type": "user", "permission": "read"}]'
```

## Implementation Plan

### Session 1: Core Permission Model

1. Add `namespace_grants` collection to Registry
2. Implement grant CRUD endpoints (bulk-first)
3. Add `GET /my/namespaces` endpoint
4. Add `resolve_permission()` to wip-auth with caching
5. Add `require_namespace_read/write/admin` dependencies
6. Update Dex config template with group assignments

### Session 2: Service Enforcement

1. Add `check_namespace_permission()` calls to document-store endpoints
2. Add to template-store endpoints
3. Add to def-store endpoints
4. Add to file endpoints
5. Test: user with `read` on namespace A, `none` on namespace B — verify B is invisible

### Session 3: Console + NLI Integration

1. Console: filter namespace picker by accessible namespaces
2. Console: namespace grant management UI
3. NLI: scope tools by user's namespace permissions
4. NLI: include permission level in system prompt

## Files to Modify

| File | Change |
|------|--------|
| `libs/wip-auth/src/wip_auth/models.py` | Add `NamespaceGrant` model |
| `libs/wip-auth/src/wip_auth/dependencies.py` | Add `require_namespace_*` dependencies |
| `libs/wip-auth/src/wip_auth/permissions.py` | New: permission resolution + cache |
| `components/registry/src/registry/models/grant.py` | New: grant document model |
| `components/registry/src/registry/api/grants.py` | New: grant CRUD endpoints |
| `components/registry/src/registry/api/namespaces.py` | Add `/my/namespaces` endpoint |
| `components/*/src/*/api/*.py` | Add `check_namespace_permission()` to write/read endpoints |
| `config/dex/config.yaml` | Add groups to static users |
| `scripts/setup.sh` | Generate Dex config with groups |

## Open Questions

1. **Cross-namespace references.** If user has `read` on namespace A and namespace B, and a document in A references a document in B — should the reference resolve? Proposal: yes, if both namespaces are accessible. References to inaccessible namespaces return "reference not found" (same as nonexistent).

2. **Service-to-service auth.** Internal service calls (e.g., document-store calling template-store) use the master API key and should bypass namespace checks. The `wip-services` group handles this — treated as privileged alongside `wip-admins`. **Implemented** (2026-04-04, commit 1e3f508).

3. **Namespace creation.** Who can create namespaces? Proposal: only `wip-admins`. Namespace creators automatically get `admin` on the new namespace.
