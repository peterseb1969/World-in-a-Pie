# API-Client Type Drift

## What It Is

API-client type drift occurs when the TypeScript client library's type definitions fall out of sync with the Python API's actual request/response schema. The API accepts or returns fields that the client types don't expose.

## Why It Matters

WIP's server-side uses `StrictModel` (`extra='forbid'`) on request models — unknown fields are rejected at the API boundary. The client-side TypeScript types are the equivalent enforcement layer for compile-time safety. When client types are incomplete:

1. **Developers use `as any` to bypass the type system.** This removes ALL type safety for that call, not just for the missing field. For sensitive fields like `deletion_mode: 'full'` (which enables permanent data destruction), the field becomes invisible to developers and bypasses compile-time checks entirely.

2. **API response data is silently lost.** If a response interface is missing a field, TypeScript consumers can't access it without casting. The data arrives from the API but is invisible to the type system.

3. **Required request fields appear optional.** If the client type omits a required field (e.g., `namespace` on `ValidateDocumentRequest`), developers don't see it at compile time and get a 422 at runtime instead.

## The Three Failure Modes

| Mode | Cause | Effect |
|------|-------|--------|
| Hidden capabilities | Response field missing from client type | Consumers can't use API features without `as any` |
| Silent data loss | Response type incomplete or missing entirely | Useful metadata (warnings, activation details) discarded |
| False optionality | Required request field missing from client type | Runtime 422 instead of compile-time error |

## How It Happens

Drift accumulates naturally:

- A Python API model gets a new field; nobody updates the TypeScript type
- A new endpoint is added with a new response model; no corresponding TS interface is created
- An inline type in a service method is written once and never updated when the API evolves
- Response types are simplified (e.g., `{ activated: string[] }` instead of the full `ActivateTemplateResponse`)

There is no automated check that catches this. The Python models and TypeScript types are in different languages in different directories with no shared schema.

## Audit Process

### When to Audit

- After adding fields to any Pydantic API model
- After creating new endpoints
- Before a minor version bump of `@wip/client`
- Periodically (the 2026-04-05 audit found 52 drifts)

### How to Audit

For each service, compare field-by-field:

1. **Python API models** (`components/<service>/src/<service>/models/api_models.py`)
2. **TypeScript type interfaces** (`libs/wip-client/src/types/<domain>.ts`)
3. **Service method signatures** (`libs/wip-client/src/services/<service>.ts`) — check for inline types

For every Pydantic model that has a corresponding TypeScript interface:

```
DRIFT: <TypeScript interface> vs <Python model>
  Field: <field_name>
  Issue: missing from client | missing from API | type mismatch
  Python: <python type>
  TypeScript: <typescript type or "missing">
```

### What to Check

- **Missing fields** — API has it, client doesn't (most common)
- **Extra fields** — client has it, API doesn't (less common, usually harmless)
- **Type mismatches** — `str` vs union literal, `dict[str, Any]` vs specific interface
- **Missing response types** — API returns a model, client has no corresponding interface
- **Inline types** — service methods using `{ foo: string }` instead of a named interface

## Historical Audit: 2026-04-05

### Scope

Audited all 5 services (registry, def-store, template-store, document-store, reporting-sync) against `@wip/client` TypeScript types.

### Findings: 52 drifts

| Category | Count | Examples |
|----------|-------|---------|
| Missing response fields | 15 | `file_references`, `template_value`, `matched_via`, `will_also_activate` |
| Missing request fields | 8 | `namespace`, `document_id`, `version`, `synonyms`, `validate_references` |
| Missing/incomplete response types | 24 | `ActivateTemplateResponse`, `DocumentCreateResponse`, all reporting-sync admin types |
| Inline types needing extraction | 5 | Service method return types |

### Fix History

| Commit | Batch | @wip/client | Changes |
|--------|-------|-------------|---------|
| `15f317e` | CASE-16 | 0.5.2 | `deletion_mode`, `allowed_external_refs` on namespace types |
| `f2732df` | CASE-16 follow-up | 0.5.3 | `namespace` on `FileEntity` |
| `b34a061` | Batch 1 — response fields | 0.6.0 | 10 missing fields across 4 services |
| `dc1c338` | Batch 2 — request fields | 0.6.1 | 8 missing fields on create/validate requests |
| `ad04a7d` | Batch 3 — response types | 0.6.2 | 6 new response interfaces + `@wip/react` 0.5.2 |
| `01b3479` | Batch 4 — reporting-sync | 0.7.0 | 17 new admin/operational type interfaces |

## Prevention

### For Backend Developers

When adding or changing fields on a Pydantic API model:

1. Check the corresponding TypeScript type in `libs/wip-client/src/types/`
2. Add the field to the TS interface
3. If there's no corresponding TS type, create one
4. Run `npm run build` in `libs/wip-client/` to verify
5. Bump the patch version

### For @wip/client Developers

When adding new service methods:

1. Create named interfaces for request and response types — don't use inline `{ ... }` types
2. Match field names exactly to the Python API model (snake_case)
3. Use union literals for constrained string fields (e.g., `'active' | 'inactive'` not `string`)

### Mapping Conventions

| Python | TypeScript |
|--------|-----------|
| `str` | `string` |
| `int` / `float` | `number` |
| `bool` | `boolean` |
| `datetime` | `string` (ISO 8601) |
| `str \| None` | `string \| null` (response) or `string?` (request) |
| `dict[str, Any]` | `Record<string, unknown>` |
| `list[str]` | `string[]` |
| `list[Model]` | `Model[]` |
| `Literal['a', 'b']` | `'a' \| 'b'` |

### File Layout

```
libs/wip-client/src/types/
  common.ts      ── BulkResultItem, BulkResponse, PaginatedResponse
  registry.ts    ── Namespace, entries, synonyms, search
  terminology.ts ── Terminology, Term, validation, import/export
  template.ts    ── Template, FieldDefinition, activation, cascade
  document.ts    ── Document, validation, table view, query
  file.ts        ── FileEntity, upload, integrity
  ontology.ts    ── Relation, traversal
  reporting.ts   ── Sync, metrics, alerts, batch sync, search, integrity, CSV export
```
