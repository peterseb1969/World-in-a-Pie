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
