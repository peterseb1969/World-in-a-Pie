# Why Synonym Resolution Matters — The Full Argument

**Origin:** Written during the synonym resolution gap audit (2026-04-04, BE-YAC-20260404-1622). Extracted from the implementation plan to preserve the reasoning independently of the task.

---

## The guarantee WIP promises

WIP's design document (`docs/design/universal-synonym-resolution.md`) establishes a simple principle: **querying with any valid synonym should behave identically to querying with the canonical ID.** The Registry stores synonyms (human-readable values, cross-instance portable keys) alongside canonical IDs. The resolution layer (`wip_auth/resolve.py`) translates any identifier to its canonical form before the service touches MongoDB.

**Important: canonical ID format is not fixed.** The default is UUID7, but this is configurable per namespace — prefix patterns (`PRJ-XXXX`), sequential IDs, and custom formats are all valid. No code should assume canonical IDs are UUIDs. The resolution layer handles all formats uniformly: it sends every ID to the Registry, which determines whether it's a canonical ID (looked up by `entry_id`) or a synonym (looked up by `composite_key` hash). Format-based shortcuts are explicitly forbidden.

This is not a convenience feature — it is a **structural integrity guarantee**. WIP exists to be guardrails for AI agents building applications. Those agents don't track opaque IDs between calls. They know human-readable names: `PATIENT`, `gender`, `AA_PROJECT`. If synonym resolution works on some endpoints but not others, agents learn they cannot trust it, and they fall back to lookup-then-act patterns on every operation. The ergonomic win disappears and the guarantee becomes meaningless.

## What happens when it's partial

CASE-02 demonstrated the failure mode. AuthorAssist (an AI-built app) called `GET /documents?template_id=AA_PROJECT`. The endpoint had a `contextlib.suppress` wrapping resolution — when it failed, the unresolved synonym `AA_PROJECT` was passed directly to MongoDB as a `template_id` filter. MongoDB returned nothing (documents store the canonical ID, not the human-readable name). The endpoint returned an empty list. Every page in the app was blank. The `contextlib.suppress` pattern is now gone, but the underlying issue — incomplete resolution coverage — was addressed in commit `3dece58` (2026-04-04).

## The three failure modes of missing resolution

1. **Silent wrong results.** An unresolved synonym reaches MongoDB. MongoDB finds nothing (or worse, finds something with a colliding value). The user gets empty results or incorrect data with no error. This is the worst mode because it's invisible.

2. **Opaque 500 errors.** An unresolved synonym reaches code that expects a canonical ID. The code throws an unexpected exception. The user sees "Internal Server Error" with no indication that the fix is trivial (use the canonical ID).

3. **Corrupted stored data.** An unresolved synonym is written to the database (e.g., `replaced_by_term_id` in a deprecation record, or field references in a template update). The synonym string is persisted instead of the canonical ID. Downstream operations that read this field and try to use it as an ID fail. The corruption persists until someone manually fixes the stored data.

## Why this must be fixed at the API boundary

The design principle in `CLAUDE.md` says: "The Registry is the identity authority." Resolution belongs at the API boundary — the moment an external identifier enters the system. Not in service internals, not in MongoDB queries, not as a fallback. At the boundary, before any business logic runs.

The helpers `resolve_or_404` (single path/query param) and `resolve_bulk_ids` (bulk request body items) exist in `wip_auth/fastapi_helpers.py` for exactly this purpose. All API-boundary endpoints now use them (as of 2026-04-04). Deeper internal resolution gaps — template-store's `_resolve_to_*` methods that bypass Registry — remain open and are documented in `docs/design/synonym-resolution-gaps.md`.
