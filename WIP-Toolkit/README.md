# WIP-Toolkit

The **operator CLI** for WIP: backup, inspect, verify, the archive transforms
(filter, prune, latest-only, strip-blobs, split), diff, restore, archive conversion, seeding,
status, and synonym backfill — a thin consumer of the platform APIs and of
`libs/wip-archive` (the archive format contract + id-remap library, a
sibling of `wip-auth`; see `docs/design/wip-archive-split.md`).

## Install (from a WIP checkout)

`wip-archive` is not on PyPI, so install it from the repo in the same pip
call; a bare `pip install -e WIP-Toolkit/` fails with "No matching
distribution found for wip-archive":

```bash
# from the repo root, with the repo venv active
pip install -e libs/wip-archive -e WIP-Toolkit/
```

`scripts/setup-backend-agent.sh` does this for you when the toolkit is
missing from the venv.

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
`start_restore` MCP tool, or `wip-toolkit restore`): `mode=restore`
preserves ids into empty namespaces, `mode=fresh` re-mints beside a live
original, `mode=merge` treats the archive as a delta. The toolkit's former
client-side `import` command was removed once CLI exports carried full
registry identity and the engine could restore them faithfully;
`wip-toolkit restore` is a thin client of that endpoint, not a second
write-path.

## CLI

```bash
wip-toolkit backup <namespace> <archive.zip> [--also NS]… [--all-namespaces] [--include-files] [--json]
wip-toolkit inspect <archive.zip> [TEMPLATE] [--target-version N] [--json]
wip-toolkit verify <archive.zip> [--strict] [--json]
wip-toolkit filter <archive.zip> <out.zip> TEMPLATE… [--boundary strict|drop|stubs|closure] [--no-relationships] [--definitions-only] [--dry-run] [--json]
wip-toolkit prune <archive.zip> <out.zip> [--dry-run] [--json]
wip-toolkit latest-only <archive.zip> <out.zip> [--dry-run] [--json]
wip-toolkit strip-blobs <archive.zip> <out.zip> [--dry-run] [--json]
wip-toolkit split <archive.zip> <out-dir> [--by namespace|island] [--dry-run] [--json]
wip-toolkit diff <a.zip> <b.zip> [--json]
wip-toolkit restore <archive.zip> [--mode restore|merge|fresh] [--dry-run] [--force] …
```

`backup` starts the server's backup engine for a namespace (and any
`--also` namespaces), follows the job and downloads the archive: complete
and restorable, every entity in every status and version. Shape it
afterwards with the transforms below. Needs admin on every namespace backed
up.

`verify` checks an archive offline, with no instance: inspect's findings,
plus every document against the template version it is pinned to, its
stored term references and its recomputed identity hash. What the archive
does not carry (a shared template or vocabulary) is listed as unverifiable,
not judged. Verdict `fail` (errors), `warn` or `pass`; exit code 1 on
`fail`, and with `--strict` on `warn` too.

`inspect TEMPLATE --target-version N` assesses migrating that template's
active documents to version N, as a migrate dry run would on the instance:
each is validated as a write against N (declared renames applied, terms
re-resolved, so a term deprecated since fails it), grouped by the version it
is on, with the failures counted. Reference and file fields are not checked.

`filter` writes a new archive with every version of the named templates'
documents and, unless `--no-relationships`, the relationship documents
whose source is one of them. Definitions always travel complete (pinned
templates, what they declare, their terminologies whole);
`--definitions-only` keeps no document. Kept rows are copied unchanged; a
reference to a document left out is never rewritten. Under `--boundary
drop` (default) that document is recorded as a referent, which a restore
resolves on the target install; `strict` refuses, `stubs` keeps the targets
one hop out, `closure` follows until nothing crosses (`--yes` when that
keeps more than half the documents). The output is verified first, and the
verdict and parameters go into its `derived_from` chain; exit 1 on `fail`.

`prune` writes a new archive without the templates nothing uses: no
document is pinned to them and no schema of a template with documents needs
them. Terminologies only those templates declared, and nothing kept uses,
go too; a terminology no template declares stays. Every document and file
travels, the output is verified first, and the pruned names are recorded in
its `derived_from` chain.

`latest-only` writes a new archive with each document's newest version,
whatever its status. A version a `pinned` reference names travels too (the
platform refuses to hard-delete such a version), every template version
stays, and a file only dropped versions referenced leaves with them. The
output records `include_all_versions: false`.

`strip-blobs` writes a new archive without file content. File rows leave
with their blobs (a file row without bytes would restore as a file with no
content); each file a kept document references is recorded as a referent,
keyed by the checksum of its bytes, so a restore points the documents at an
install's own copy of that file and reports it as unresolved elsewhere.

`split` writes an archive's parts into a directory: `<ns>.zip` per
namespace (`--by namespace`, default), or `island-<n>.zip` per island
(`--by island`: templates joined by references, with their documents,
files and terminologies; a shared terminology is copied into each).
Terminologies no island uses, and documents whose template the archive
does not carry, go into `unattached.zip`. A reference from one part into
another becomes a referent. Each part is verified and records its own hop.

`diff` compares two archives row by row: per namespace and entity type,
what was added, removed or changed (the differing fields named for a
sample), plus referents, blobs and both `derived_from` chains. Exit 0 when
identical, 1 when they differ, 2 on an unreadable archive.

Every transform takes `--dry-run`: the same run on scratch space, the
verify of what would be written included, with nothing written to the
output path. It reports the counts and the verdict the real run would
record.

`restore` runs the same check before uploading: an error refuses the upload
unless `--force`, which also passes the server's refusal of an archive whose
own verification failed. It then uploads the archive, starts the server job
and follows it to the end (`--no-wait` returns at once; exit code 1 when the
job fails). Merge
options apply to `--mode merge` only: `--on-clash skip|overwrite|newer`,
`--older-versions retire|keep` (whether the target's older versions are
soft-deleted when the archive's version is put on top; default `retire`),
`--add-missing`, `--extend-terminologies`. `--allow-missing-identity`
(`--mode restore` only) restores an archive that carries entities but no
registry identity rows, which the server otherwise refuses because nothing
restored would resolve by id.

`export` (the old client-side collector) is deprecated: it prints a
warning naming `backup` and the transforms, and is removed in the next
release. Export writes a 1-namespace v3 archive, including the raw registry
identity rows (fetched through the Registry's admin-gated
`POST /entries/export`) that a server-side restore re-inserts verbatim.

## Key modules

| Module | Purpose |
|---|---|
| `wip_toolkit/export/` | the CLI export pipeline (collector, closure, exporter) |
| `wip_toolkit/convert_archive.py` | v2.0 → v3 converter (lib + CLI) |
| `wip_toolkit/verify.py` | the offline verify behind `verify` and `restore`'s pre-upload gate |
| `wip_toolkit/transform_view.py` | what every transform verb prints, and its `--json` |
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
