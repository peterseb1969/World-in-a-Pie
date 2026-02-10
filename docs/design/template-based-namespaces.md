# Template-Based Namespaces

**Status:** Future idea — not urgent, current implementation is sufficient.

## Current State

Namespaces have a fixed structure: every namespace automatically gets 5 ID pools (terminologies, terms, templates, documents, files) with hardcoded ID formats. The pool types and formats are baked into `registry/models/namespace.py` (computed properties) and `registry/api/namespaces.py` (ID_POOL_CONFIGS).

## Idea

Define namespace structure via an immutable template rather than hardcoding it:

- A **namespace template** would declare which entity types (pools) the namespace contains, their ID formats, and default isolation rules.
- Creating a namespace would reference a specific template, which generates the corresponding pools.
- The template would be **immutable** once any namespace references it (same pattern as document templates).
- Different use cases could have different namespace shapes — e.g., a lightweight namespace with only documents and files, or one with a custom 6th entity type.

## Benefits

- Extensible without code changes — add new entity types by creating a new namespace template.
- Different tenants/use cases can have different namespace configurations.
- Template immutability provides a stable contract for existing namespaces.

## Considerations

- Migration path from current hardcoded structure to template-driven.
- The default "wip" namespace template would replicate today's 5-pool structure.
- Adds complexity that isn't needed while WIP only uses the standard entity types.
