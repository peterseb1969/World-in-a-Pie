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
- `wip_archive.referents` — the out-of-archive referent table: the Registry
  rows of entities an archive references but does not carry.
- `wip_archive.model` — `ArchiveModel`, the offline analysis behind
  `inspect` and `verify`: entity index, reference graph, closures, islands,
  findings.
- `wip_archive.definitions`, `wip_archive.identity`,
  `wip_archive.validation`, `wip_archive.document_check`,
  `wip_archive.migratability` — the offline checks: full definitions with
  inheritance resolved, the identity hash, the document validator, the
  per-document check `verify` runs, and migration assessment.
- `wip_archive.filter` — the `filter` plan: selection by template, the
  boundary policies, definitions closure.
- `wip_archive.prune` — the `prune` plan: templates nothing uses, and the
  terminologies only they declared.
- `wip_archive.transform` — `run_transform`, the core every shaping
  transform shares: copies the rows a transform keeps verbatim, moves cut
  entities into the referent table, verifies the output and records the
  `derived_from` hop.

## Who consumes it

- `components/document-store` — the restore/backup engine, in-process.
- `WIP-Toolkit` — the operator CLI (export, inspect, verify, restore,
  convert-archive).

Design: `docs/design/wip-archive-split.md`. Tests:
`./scripts/wip-test.sh wip-archive`.
