# Merge restore: aligning template versions across installs

**Status:** approved by Peter, 2026-11-07 (decisions below); implemented.
The option is on every merge surface: the restore API (both routes), the MCP
`start_restore` tool, `@wip/client` 0.85.0 `startRestore`, and
`wip-toolkit restore --align-templates` (WIP-Toolkit 0.25.8).

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

A merge option, `align_templates`, off by default. With it, pass 1 treats
templates as follows.

1. **Match by value.** Each archive template matches the target template of
   the same value; the mapping archive `template_id` → target `template_id`
   is recorded once per value, not per version.
2. **Map each archive version onto a target version by schema.** An archive
   version maps to the target's version whose schema it equals under the
   defaults rule (fields, identity_fields, rules, usage, versioned, endpoint
   lists), else to the target's latest active version **if** the archive
   version's schema is compatible with it. "Compatible" is the migration
   eligibility verdict template create already reports in its schema diff
   (`docs/api-conventions.md`, template upsert): added optional fields are
   eligible, and so are removed fields that are empty in every arriving
   document pinned to that version; type changes, newly-required fields,
   modified fields, and any change to identity fields, rules, usage,
   `versioned` or edge endpoints are not.
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
be placed. It is on the job's result under
`namespaces.<ns>.template_alignment` (`templates[].versions[]` with
`archive_version`, `target_version`, `rows`, `history`, `reason`;
`unplaceable_documents`, capped at 200, with `unplaceable_count` and
`unplaceable_truncated`), and on the merge's restore provenance.

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

## Decisions (Peter, 2026-11-07)

1. **Schema-level check only.** Step 2 decides per version, from the two
   schemas; the merge does not validate each document's data. Data that has
   to change to fit the target goes through `wip-archive migrate`, then
   re-export, then merge.
2. **Refused for a merge from the same install.** There the versions are
   already one entity's coordinates and line up by number, so the option has
   nothing to do and is refused rather than ignored.
3. **The option is named `align_templates`.**

## Implementation choices (for review)

These follow from the decisions above; the design did not spell them out.

- **Detecting "the same install".** An archive records no install identity
  (`source_install` carries format versions, `source_host` is a hostname that
  changes per pod). The signal is the template itself: an archive template
  whose `template_id` equals the target's template of the same value was
  minted by the same Registry, so its versions are coordinates on the same
  entity. Such a template is refused under `align_templates` ("merge without
  align_templates").
- **A document is placed whole or not at all.** A document's chain carries
  every version row, and each row pins its own template version. If any row
  pins a version that maps nowhere, the document is listed as unplaceable
  and not written: writing it without those rows would drop history, and
  writing them would pin versions the target does not have.
- **Renames are not followed.** A field the target renamed reads as one
  removed and one added field. The removed one is eligible only if empty in
  the arriving documents; moving data from the old name to the new one is a
  data change, which is `wip-archive migrate`'s job.
- **Version references inside templates follow the alignment.** A template's
  `extends_version` and a field's `template_ref_version` /
  `array_template_ref_version` name versions of another template; they are
  compared through that template's alignment, not as raw numbers.
