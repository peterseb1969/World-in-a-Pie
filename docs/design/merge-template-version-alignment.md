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

An export carries the template versions its documents and carried definitions
need — a version a document row pins, or a carried version extends or nests,
travels even when it is inactive (`BackupEngine._template_versions_to_leave_out`;
`include_inactive_templates` keeps every version regardless). The source's
retired history therefore arrives in the archive, and is compared like a live
definition.

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
   (`unplaceable_documents`, with its template and, per version, the
   reason) and not written. Only rows the merge writes count. A document
   the target already holds (same `document_id`, or the same identity on
   the aligned target template) writes nothing under `on_clash=skip`, nor
   when the two copies are identical, nor under `newer` when the archive's
   copy is not strictly newer: it neither decides whether a version can be
   placed nor is listed (`held_by_target_count`). One the archive wins under
   `overwrite` or `newer` writes only its head row, appended on top of the
   target's copy, so only that row pins a version; the rest of its archived
   chain counts as `held_rows`.
4. **History versions are not definitions to add.** An archive version that
   no arriving document pins (normally the source's retired history, or a
   draft nobody activated) is neither compared nor added, whatever its
   status. The versions decided are the ones arriving documents pin, and the
   versions of aligned templates those name (an `extends_version`, a
   `template_ref_version`), transitively. Among them, a target draft is never
   a version to map onto, and an archived draft is refused (no document can
   be written against a draft, so an archive whose documents pin one does
   not hold what it claims). See *Settling order and scope* below.
5. **Different identity is still a fork.** An archive template whose
   `identity_fields` differ from the target's is refused, as today; that is a
   different template, not a version of this one.

The dry run reports the whole alignment: per template, which archive versions
map to which target versions, which are history, and which documents cannot
be placed. It is on the job's result under
`namespaces.<ns>.template_alignment` (`templates[].versions[]` with
`archive_version`, `target_version`, `outcome`, `rows`, `held_rows`,
`history`, `reason`; `outcome` is one of `mapped`, `unplaced` (used, no target
version takes it: its documents are left out), `held` (used only by documents
the target already holds), `history` (used by nothing that arrives) and
`refused`;
`unplaceable_documents`, capped at 200, with `unplaceable_count`,
`unplaceable_truncated` and `held_by_target_count`), and on the merge's
restore provenance. A merge the alignment refuses carries the same account on
its refusal (`template_alignment` beside `incompatibilities`). A merge with `skip_documents` settles the versions
against no documents, so it lists none.

## What this does not do

- **Data-level validation of each document.** Step 2 decides at schema level,
  per version, not per document. A document that is valid under its archived
  version is valid under a compatible target version by construction, except
  where the target adds a constraint the schema comparison cannot see (a
  narrowed enum, a new pattern). Those cases are refused at step 2 as
  incompatible, never discovered per document. Moving data across an
  incompatible change is not solved by running `wip-toolkit migrate` and
  merging the result: on a `versioned: true` template `migrate` leaves each
  document's superseded row pinned to the old version, which the archive
  still carries and pass 1 still has to place. The procedure that does work
  — converge a version of the TARGET's template that can take the archive's
  documents, merge with `align_templates`, then retire that version — is in
  `docs/design/restore-modes-merge-and-new-namespace.md` and pinned by
  `components/document-store/tests/test_case_1159.py`.
- **Changing the target's templates.** The target's versions are never
  edited; `add_missing` keeps its current meaning for templates the target
  lacks by value.

## Decisions (Peter, 2026-11-07)

1. **Schema-level check only.** Step 2 decides per version, from the two
   schemas; the merge does not validate each document's data. Data that has
   to change to fit the target is not converged by migrating the archive and
   merging it again (a `versioned: true` template keeps its superseded rows
   pinned to the old version, which then has to map too); see
   `docs/design/restore-modes-merge-and-new-namespace.md` for the
   target-side converge that does.
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
  entity, and already line up. Such a template is matched by `(value,
  version)` under `align_templates`, as without the option, so an archive
  that mixes it with another install's templates is aligned template by
  template. (It used to be refused, with advice such an archive could not
  follow.)
- **A document is placed whole or not at all.** A document's chain carries
  every version row, and each row pins its own template version. If any row
  pins a version that maps nowhere, the document is listed as unplaceable
  and not written: writing it without those rows would drop history, and
  writing them would pin versions the target does not have.
- **Renames are not followed.** A field the target renamed reads as one
  removed and one added field. The removed one is eligible only if empty in
  the arriving documents; moving data from the old name to the new one is a
  data change, which is `wip-toolkit migrate`'s job.
- **Version references inside templates follow the alignment.** A template's
  `extends_version` and a field's `template_ref_version` /
  `array_template_ref_version` name versions of another template; they are
  compared through that template's alignment, not as raw numbers. Templates
  can name each other's versions, or their own, so they are not mapped one
  after another. Each archive version starts with every target version its
  schema matches with those references left aside (the same schema, or the
  target's preferred version when compatible). A candidate is dropped while
  a version it pins is not a candidate of the archive version it stands for,
  until nothing is dropped. Each version then takes a candidate, preferred
  first, backing out of a choice that leaves another version with none, so a
  mapping is not missed because the first preference did not work out. Every
  version may also stay unplaced, as its last option, and the assignment kept
  is the one that places the most versions, so one version that fits nowhere
  never costs the others their mapping. Every
  mapping is written before any version is compared, and each mapped version
  is checked against its target version with the other mappings in place,
  which unmaps one that no longer holds: **every version reported as mapped is
  consistent with every other mapping.** A version whose candidates were all
  dropped is never mapped onto a target version by its archived number —
  those numbers are another install's — it is reported unplaceable, with the
  reason: that its version references do not line up with the other
  templates', or what differs from the target's preferred version.

## Settling order and scope (Peter, 2026-11-16)

Alignment drew findings in five places within two days, each from deciding
one condition on the evidence pass 1 happened to have. Pass 1 sees template
rows only. Which versions the arriving documents pin is known once the
documents are read, and which documents the target already holds is known
only once pass 2 has matched files and documents to a fixed point: a
document identity can hash another archived document's id, or a file's,
which only that matching turns into the id the target holds. So:

1. **Pass 1 matches aligned templates by value and decides no version.** The
   template-level refusal (different `identity_fields`, `usage`,
   `versioned`) stays in pass 1.
2. **Held documents come from pass 2's own matching.** The merge plans
   files and documents against the target on copies, as pass 2 does, and
   takes the documents it finds as clashes; each is decided by the clash
   policy's own rule (`skip` writes none; `overwrite` and `newer` append the
   head of the ones the archive wins, `_archive_wins`). That
   is the only rehash; nothing else hashes identities ahead of it. Re-pinning
   changes only `template_version`, and the identity hash has no version in
   it, so the matching never depends on the version mapping.
3. **Then the versions are decided:** the ones the rows the merge writes pin,
   and the versions those name, transitively. Everything else is history.
4. **Status is judged on those versions only.** A target draft is never a
   candidate; an archived draft among them refuses the merge; active against
   inactive maps, and the target's status is kept and reported.
5. **The search places as many versions as it can.** Each version may stay
   unplaced as its last option; a version that fits nowhere is reported
   `unplaced` and does not stop the search for the others.
6. **One account per version.** Every archived version of an aligned
   template is reported with its `outcome` and reason, on the merge's result
   and on a refusal alike.

The cost: with `align_templates`, files and documents are planned twice,
once to find the held documents and once for the merge, under every clash
policy.

