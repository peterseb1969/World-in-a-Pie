# WIP-Toolkit

Python toolkit for WIP **archive I/O** (backup/restore + export/import) and a small
CLI. The document-store backup engine and the `wip` CLI both read/write the archive
format defined here.

## Archive format — v3 (multi-namespace)

The archive is **v3** and carries one or more namespaces:

```
my-archive.zip
├── manifest.json                       # format_version "3.0", namespaces: [...]
├── namespaces/
│   ├── <ns-a>/
│   │   ├── terminologies.jsonl
│   │   ├── terms.jsonl
│   │   ├── term_relations.jsonl
│   │   ├── templates.jsonl
│   │   ├── documents.jsonl
│   │   ├── files.jsonl
│   │   └── registry_entries.jsonl
│   └── <ns-b>/ …
└── blobs/<file_id>                      # flat; file_ids are globally-unique UUID7
```

Blobs are flat by design (globally-unique ids → no cross-namespace collision; blob
I/O stays namespace-agnostic). A single-namespace archive is just a 1-namespace v3
archive.

The engines (document-store `DirectBackupEngine`/`DirectRestoreEngine`) speak **only
v3** — there is no v2.0 read path.

## ⚠️ Converting pre-v3 archives

A pre-v3 (v2.0 *flat*) archive — anything produced before 2026-06-29 — **cannot be
read by the v3 restore engine.** Convert it once:

```bash
python -m wip_toolkit.convert_archive OLD.zip NEW.zip
```

One-way `v2.0 → v3` (produces a 1-namespace v3 archive). It rewrites the root-level
`<entity>.jsonl` files under `namespaces/<ns>/`, carries blobs through flat, and
refuses an input that is already v3. **If you have stored backups from before the v3
change, convert them before relying on a restore.**

## Multi-namespace backup/restore

Driven through the document-store REST API (not the toolkit directly):

- **Backup** `POST /api/document-store/backup/namespaces/{ns}/backup` with body
  `{"namespaces": [...], "all_namespaces": <bool>, …}`. `all_namespaces: true` backs
  up every registry namespace (including `wip`). Admin is required on each.
- **Restore** uploads an archive; each namespace restores **to itself** and each
  target must be **empty** (identity-only — no namespace remap). A single-namespace
  archive may be redirected via `target_namespace`.

## CLI (single-namespace export/import)

```bash
wip export <namespace> <archive.zip> [--include-files] …
wip import <archive.zip> [--target-namespace <ns>] …
```

The CLI is single-namespace per invocation; it produces/consumes a 1-namespace v3
archive. (Multi-namespace CLI *inspect* is not yet implemented.)

## Key modules

| Module | Purpose |
|---|---|
| `wip_toolkit/models.py` | `Manifest`, `NamespaceEntry`, `EntityCounts`, … (pydantic) |
| `wip_toolkit/archive.py` | `ArchiveWriter` / `ArchiveReader` — v3 ZIP+JSONL I/O |
| `wip_toolkit/convert_archive.py` | v2.0 → v3 converter (lib + CLI) |
| `wip_toolkit/export/`, `import_/` | the CLI export/import pipeline |

## Tests

```bash
cd WIP-Toolkit && PYTHONPATH=src python -m pytest
```

## See also

- `docs/design/backup-restore-redesign.md` — the backup/restore design (the **v3
  Multi-namespace archives** section at the top supersedes the single-namespace dump
  format described below it).
