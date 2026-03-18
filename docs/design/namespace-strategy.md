# Namespace Strategy Guide

How to organize data across namespaces in WIP, balancing shared vocabularies with per-app data isolation.

## The Golden Rule

> Terminologies are language, namespaces are boundaries. Everyone speaks the same language, but each household has its own walls.

## Namespace Roles

### Shared Namespace (`wip`)

The `wip` namespace holds **shared terminologies** — controlled vocabularies that multiple apps agree on.

Examples:
- CURRENCY (EUR, USD, CHF, ...)
- COUNTRY (ISO 3166)
- LANGUAGE (ISO 639)
- DOCUMENT_STATUS (draft, active, archived)

Properties:
- **Everyone can read.** In open isolation mode, any namespace can reference `wip` entities without explicit configuration.
- **Admins write.** Only users/groups with `write` or `admin` grants on `wip` can create or modify terminologies.
- **No app-specific data.** Templates and documents belong in app namespaces, not here.

### App Namespaces (`finance`, `dnd`, `fedlex`, ...)

Each application, constellation, or use case gets its own namespace. This is where templates, documents, and files live.

Examples:
- `finance` — Receipt Scanner, financial transactions
- `dnd` — D&D character sheets, campaigns, items
- `fedlex` — Swiss law expressions, legal works

Properties:
- **Templates and documents are namespace-scoped.** A RECEIPT template lives in `finance`, not `wip`.
- **Access is controlled per user/group.** Grants determine who can read, write, or administer.
- **Cross-namespace term references just work.** A `currency` field on a `finance` template references the CURRENCY terminology in `wip` — no special configuration needed.

## Cross-Namespace References

### How It Works

WIP has two layers controlling cross-namespace access:

| Layer | Controls | Mechanism |
|-------|----------|-----------|
| **Isolation mode** | What can reference what | `isolation_mode` + `allowed_external_refs` on namespace |
| **Authorization** | Who can read/write | Grants on namespace (user, group, API key) |

These are independent. Isolation mode governs data relationships. Authorization governs user access.

### Isolation Modes

**Open mode** (default): The namespace can reference entities in `wip` and any namespace listed in `allowed_external_refs`.

```
finance (open, allowed_external_refs: [])
  → Can reference: finance + wip
  → Cannot reference: dnd, fedlex, etc.

finance (open, allowed_external_refs: ["fedlex"])
  → Can reference: finance + wip + fedlex
```

**Strict mode**: Only same-namespace references, plus explicit allowlist. The `wip` namespace is NOT automatically allowed.

```
classified (strict, allowed_external_refs: ["wip"])
  → Can reference: classified + wip only
  → Every external ref must be explicitly listed
```

### What Gets Checked

Reference validation runs at **document creation**, not template creation. A template can declare any terminology reference — the check happens when a document tries to use it.

| Reference Type | Checked Against |
|----------------|-----------------|
| Term references (terminology_id) | Document namespace isolation mode |
| File references | Document namespace isolation mode |
| Template extends (parent template) | Template namespace isolation mode |
| Document-to-document references | Document namespace isolation mode |

### Authorization and References

A user writing a document in `finance` can reference a `wip` terminology **even without an explicit read grant on `wip`**. This is intentional — shared vocabularies are the common language. The isolation mode permits the reference; the authorization layer governs direct access to the namespace's data.

Put differently:
- You need a **grant** to list, view, or modify entities in a namespace.
- You do **not** need a grant to reference entities from a namespace that your isolation mode allows.

## Recommended Setup

### For a New WIP Instance

After `setup.sh`, the `wip` namespace exists with shared terminologies. Create app namespaces as needed:

```bash
# Create an app namespace
curl -X POST http://localhost:8001/api/registry/namespaces \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '[{
    "prefix": "finance",
    "description": "Financial data — receipts, transactions, accounts",
    "isolation_mode": "open"
  }]'

# Grant access to a user group
curl -X POST http://localhost:8001/api/registry/namespaces/finance/grants \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '[
    {"subject": "wip-editors", "subject_type": "group", "permission": "write"},
    {"subject": "wip-viewers", "subject_type": "group", "permission": "read"}
  ]'
```

### Default Grants (seeded by setup.sh)

| Group | `wip` namespace | App namespaces |
|-------|----------------|----------------|
| `wip-admins` | admin (superadmin) | admin (superadmin) |
| `wip-editors` | write | grant per namespace |
| `wip-viewers` | read | grant per namespace |

The `wip-admins` group has superadmin access to all namespaces via code — no grants needed. For `wip-editors` and `wip-viewers`, setup.sh seeds grants on `wip` only. App namespace grants must be created explicitly.

### Multi-Tenant Pattern

For scenarios where different users should see different app data but share vocabularies:

```
wip (shared)          ← everyone reads, admins write
├── CURRENCY
├── COUNTRY
└── LANGUAGE

finance (app)         ← finance team: write; others: no access
├── RECEIPT template
├── FIN_TRANSACTION template
└── documents, files

legal (app)           ← legal team: write; auditors: read
├── CONTRACT template
├── LEGAL_ENTITY template
└── documents, files
```

Each team works in their namespace. They all share the same CURRENCY and COUNTRY terminologies from `wip`. A user with access only to `finance` cannot see `legal` documents — they get a 404, not a 403 (no namespace leaking).

## Where Terminologies Live

**Shared terminologies → `wip` namespace.** If two or more apps might use it, it belongs in `wip`. Currency, country, language, status codes — these are vocabulary, not data.

**Domain-specific terminologies → app namespace.** If only one app uses it, keep it local. A D&D alignment terminology (Lawful Good, Chaotic Evil) belongs in `dnd`, not `wip`.

**Migration path:** Start domain-specific. If a second app needs the same terminology, promote it to `wip`. The terminology_id stays the same — update the namespace field and adjust grants.

## NLI Considerations

When the Natural Language Interface (P7) goes live, namespace authorization scopes what the NLI can access per user:

- The NLI's system prompt includes the user's accessible namespaces and permission levels.
- Read-only users can query but not modify.
- The NLI cannot access namespaces the user has no grant for.
- Shared terminologies in `wip` are available to all NLI users for lookups and validation.
