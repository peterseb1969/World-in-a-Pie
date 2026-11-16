---
section: fixes
cases: [1236]
---

### A merge plan reports the same content under another id as unchanged, not as a clash

Merging an archive from another install (or into a redirected namespace)
matches files by checksum and Registry entries by their keys, then compared
the two copies field by field, ids included. A file with the same bytes under
the other install's `file_id`, or an entry under another `entry_id`, was
counted as a clash in the plan and the dry run. A file's or entry's own id, and
the fields derived from it or kept as bookkeeping (a file's `storage_key`,
`uploaded_at`, `uploaded_by` and `reference_count`; an entry's
`search_values` and `revision`), no longer count; the id difference is
reported as `remapped`. Lifecycle differences (`status`) still count. Nothing a
merge writes changes: these types never overwrite on a clash.

An entry copied from another install still counted as a clash on its
synonyms (each carries its own `created_at`, `created_by` and a
`composite_key_hash`, and the list may be in another order) and on
`actor_note`. Synonyms are now compared by content, as a set, and
`actor_note` goes with the audit fields. `merged_into` and a synonym's
`merged_from`, which name entries by id, are rewritten through the merge's id
mappings like the ids in the keys, so an entry the target holds merged into
the same entry compares identical, and an inserted entry points at the
target's id. A missing synonym, a synonym's status and a merge into another
entry still count.
