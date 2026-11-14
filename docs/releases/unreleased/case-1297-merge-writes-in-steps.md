---
section: fixes
cases: [1297, 1096]
---

### A multi-namespace merge writes definitions everywhere first, and a failure after a write says what was written

A merge used to write one namespace completely (definitions, reporting gate,
documents) before it started the next. Namespace B's reporting gate could
therefore refuse after namespace A had been fully merged, and the job
reported a plain refusal. It now writes in three steps across all of the
archive's namespaces:

1. every namespace's definitions and their Registry entries (whose key
   claims are now written with them),
2. every namespace's reporting structure gate,
3. every namespace's documents, files and remaining entries.

A gate that refuses now does so before any document is written anywhere.
Nothing written is undone: any failure after the first write fails the job
with `result_kind: merge_partial` and `details.written`, naming per target
what it received. A merge's dry run still cannot see the reporting gate,
which needs the templates written.

Also fixed, for `align_templates`:

- A merged document that references one the alignment left out as
  unplaceable used to refuse the merge with "Include the missing entities in
  the archive", but those documents are in the archive. The refusal now names
  them and the real cause (`result_kind: unplaceable_referenced`).
- Under `on_clash=skip`, a document the target already holds (same id, or the
  same identity on the aligned template) is never written. A field it fills
  no longer makes its version unplaceable for the documents that do arrive.
  The report counts such documents (`held_by_target_count`).
- A template that is the same on both sides (one install minted it) is now
  matched by version number instead of refusing the merge. An archive that
  mixes such templates with another install's could not be merged at all
  before: with the option the shared one was refused, without it the other.
- With `skip_documents` the alignment no longer reads the archive's documents
  or lists unplaceable ones.
- Aligned template versions are counted as `aligned`, no longer as
  "already identical" (`unchanged`). A label, description or other cosmetic
  field that differs from the target version they land on is reported as
  "target's … kept".

**Corrections to the earlier `align_templates` note** in this release:
- The command that reshapes documents is `wip-toolkit migrate`, not
  `wip-archive migrate`.
- A template both sides share is no longer refused (above).
- A multi-namespace merge could refuse after writing (above), so "a refusal
  leaves every target untouched" was not true. It now holds for the reporting
  gate as far as documents go.
