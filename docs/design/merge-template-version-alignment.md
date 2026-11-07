# Merge restore: aligning template versions across installs

**Status:** proposed, awaiting approval. Nothing here is implemented.

## The problem

Pass 1 of a merge matches templates by `(value, version)`. Version numbers are
coordinates on one install's history, not a property of the schema: an
install that evolved `DOCUMENT` through six versions holds v1–v6; a fresh
bootstrap of the same app holds v1, with today's schema. Merging the first
into the second compares the archive's old v1 with the target's current v1
(they differ, so the merge is refused) and finds no counterpart for v2–v6
(refused, or with `add_missing` inserted beside the target's unrelated v1
under their archived numbers).

An export carries every version, inactive ones included, because documents
pin them (`BackupEngine._build_query`). The source's retired history therefore
arrives in the archive and is compared like a live definition.

Observed on dev-test: a kb archive with 11 of 27 templates at v2–v6 could not
be merged into a freshly bootstrapped kb at all.

## What stays fixed

- **No silent re-pinning.** Moving a document to another template version
  changes its contract. The platform never does it implicitly
  (`docs/design/template-identity-unification.md`: migration is always
  explicit). Whatever is proposed here is an opt-in strategy; the default
  merge keeps refusing.
- **Identity is safe to re-pin within one template.** `identity_fields` are
  immutable across versions, and a document's Registry key is
  `(namespace, identity_hash, template_id)` with no version in it, so a
  re-pin changes neither.
- **Report, never force.** A document no target version can take is listed
  and left out, not written.

## Proposal

A merge option, `align_templates` (name open), off by default. With it, pass 1
treats templates as follows.

1. **Match by value.** Each archive template matches the target template of
   the same value; the mapping archive `template_id` → target `template_id`
   is recorded once per value, not per version.
2. **Map each archive version onto a target version by schema.** An archive
   version maps to the target's version whose schema it equals under the
   defaults rule (fields, identity_fields, rules, usage, versioned, endpoint
   lists), else to the target's latest active version **if** the archive
   version's schema is compatible with it. "Compatible" is the migration
   eligibility verdict template create already reports in its schema diff
   (`docs/api-conventions.md`, template upsert): additive or rename-only
   changes are eligible; type changes, newly-required fields and modified
   fields are not.
3. **Documents follow their version.** Each archive document is written
   pinned to the target version its archived version mapped to. A document
   whose version mapped nowhere is listed in the plan
   (`unpinnable_documents`, per template and version, with the reason) and
   not written.
4. **History versions are not definitions to add.** An archive version that
   no arriving document pins (normally the source's retired history) is
   neither compared nor added. A version some arriving document pins is
   handled by step 2 whatever its status.
5. **Different identity is still a fork.** An archive template whose
   `identity_fields` differ from the target's is refused, as today; that is a
   different template, not a version of this one.

The dry run reports the whole alignment: per template, which archive versions
map to which target versions, which are history, and which documents cannot
be placed.

## What this does not do

- **Data-level validation of each document.** Step 2 decides at schema level,
  per version, not per document. A document that is valid under its archived
  version is valid under a compatible target version by construction, except
  where the target adds a constraint the schema comparison cannot see (a
  narrowed enum, a new pattern). Those cases are refused at step 2 as
  incompatible, never discovered per document. Moving data across an
  incompatible change stays the toolkit's job: `wip-archive migrate`, then
  re-export, then merge.
- **Changing the target's templates.** The target's versions are never
  edited; `add_missing` keeps its current meaning for templates the target
  lacks by value.

## Open questions for approval

1. Is a schema-level compatibility check (step 2) enough, or must the
   merge validate each document's data against the target version?
2. Should `align_templates` also apply when the source is the same install
   (a same-install merge already lines up by version), or be refused there
   as meaningless?
3. The option's name.
