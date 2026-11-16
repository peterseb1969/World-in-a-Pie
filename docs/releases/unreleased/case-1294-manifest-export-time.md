---
section: action-required
cases: [1294]
packages: [wip-archive, wip-toolkit]
---

### An archive's manifest has no export time unless the archive records one

`Manifest.exported_at` in `wip_archive.models` is now `datetime | None`,
`None` when the archive does not say. It used to default to the moment the
manifest was read, so every reader that did not check which fields were set
reported its own time as the archive's export time. The writers that make
an archive record it: document-store's backup engine, and the toolkit's
`export`, which used to rely on the default. `convert-archive` records it
only when the source archive has one (below).

- `convert-archive` copies a v2 archive's export time only when it has one.
  It used to write the moment of the conversion into the v3 manifest, and a
  later restore reported that as the export time.
- `inspect` shows a missing export time as `unknown`, and `inspect --json`
  and `verify --json` report it as `null` (they used to report the time of
  the inspect). That re-types the field, so their output moves to schema 2.0.
- A restore's provenance and namespace description already said "an
  unrecorded time" for such an archive; they now read the field directly.

The archive format is unchanged (`format_version` stays 3.1): archives that
record an export time read as before.

**Action:** code that reads `Manifest.exported_at`, or `archive.exported_at`
in `inspect --json` / `verify --json`, must handle `None` / `null`. A
consumer that checks the JSON `schema` must accept 2.0.
