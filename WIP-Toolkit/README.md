# WIP-Toolkit

The **operator CLI** for WIP: export, inspect, archive conversion, seeding,
status, and synonym backfill — a thin consumer of the platform APIs and of
`libs/wip-archive` (the archive format contract + id-remap library, a
sibling of `wip-auth`; see `docs/design/wip-archive-split.md`).

## Archive format — v3 (multi-namespace)

The format is owned by `wip_archive` and shared with the document-store
backup/restore engine:

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
│   │   └── registry_entries.jsonl      # raw identity rows — what makes the
│   └── <ns-b>/ …                       #   archive restorable by the engine
└── blobs/<file_id>                      # flat; file_ids are globally-unique UUID7
```

Blobs are flat by design (globally-unique ids → no cross-namespace collision; blob
I/O stays namespace-agnostic). A single-namespace archive is just a 1-namespace v3
archive. The engines speak **only v3** — there is no v2.0 read path.

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

## Importing an archive

There is **one import write-path: the server restore engine.** Upload the
archive to the target instance's restore endpoint
(`POST /api/document-store/backup/namespaces/{ns}/restore`, or the
`start_restore` MCP tool): `mode=restore` preserves ids into empty
namespaces, `mode=fresh` re-mints beside a live original, `mode=merge`
treats the archive as a delta. The toolkit's former client-side `import`
command was removed once CLI exports carried full registry identity and
the engine could restore them faithfully.

## CLI (single-namespace export)

```bash
wip-toolkit export <namespace> <archive.zip> [--include-files] …
wip-toolkit inspect <archive.zip>
```

Export writes a 1-namespace v3 archive, including the raw registry
identity rows (fetched through the Registry's admin-gated
`POST /entries/export`) that a server-side restore re-inserts verbatim.

## Key modules

| Module | Purpose |
|---|---|
| `wip_toolkit/export/` | the CLI export pipeline (collector, closure, exporter) |
| `wip_toolkit/convert_archive.py` | v2.0 → v3 converter (lib + CLI) |
| `wip_toolkit/seed.py`, `status.py`, `backfill.py` | operator utilities |
| `libs/wip-archive` (separate lib) | `ArchiveReader`/`ArchiveWriter`, `Manifest` models, `IDRemapper` |

## Tests

```bash
./scripts/wip-test.sh wip-toolkit
```

The integration suite (`tests/integration/`) mounts the four services
in-process and round-trips a CLI export through the server restore engine.

## See also

- `docs/design/wip-archive-split.md` — where the format contract lives and why
- `docs/design/backup-restore-redesign.md` — the backup/restore design
