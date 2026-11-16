---
section: fixes
cases: [1398]
packages: [wip-client]
---

### A document write's actor is the authenticated caller; the caller's text is `actor_note`

Document-store has always recorded the authenticated caller as a document's `created_by` / `updated_by`, but its request models still offered `created_by` (create) and `updated_by` (delete) as the actor, and dropped what a caller sent there without a word. As in the Registry, that text is now kept: a create or upsert stores it as the new version's `actor_note`, an in-place overwrite (`versioned: false`) replaces the note with its own, a delete stores its `updated_by` text as the retired row's `actor_note`, and a write that sends no text — a PATCH always — leaves none. A version retired because a newer one replaced it keeps its note. `actor_note` is returned on documents (`GET /documents/{id}`, listings, relationships) and is never the actor. In `@wip/client`, `Document.actor_note` is typed and `createDocument`'s `created_by` / `deleteDocument`'s `updatedBy` are documented as this note.
