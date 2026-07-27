# wip-archive

The WIP backup **archive format contract** and the **fresh-restore id-remap
pass**, as a shared Python library. A sibling of `wip-auth`, consumed the
same way: an editable path dependency (`-e ../../libs/wip-archive`),
bind-mounted in dev stacks, baked into release images.

## Modules

- `wip_archive.archive` — `ArchiveReader` / `ArchiveWriter`, the `.zip`
  archive layout in both directions. This is the format contract: the
  document-store backup/restore engine reads and writes archives through
  it, and the WIP-Toolkit operator CLI consumes it for export/inspect.
- `wip_archive.models` — the pydantic shapes shared across that boundary
  (`Manifest`, `EntityCounts`, `NamespaceConfig`, `NamespaceEntry`,
  `ProgressEvent`, …).
- `wip_archive.remap` — `IDRemapper`, the reference-rewrite pass a fresh
  restore runs over every entity payload (recursive over document data:
  arrays, nested objects).

## Who consumes it

- `components/document-store` — the restore/backup engine, in-process.
- `WIP-Toolkit` — the operator CLI (export, inspect, convert-archive).

Design: `docs/design/wip-archive-split.md`. Tests:
`./scripts/wip-test.sh wip-archive`.
