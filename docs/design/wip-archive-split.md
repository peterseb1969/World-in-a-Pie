# wip-archive: the archive format and id-remap library

**Status:** designed, pending implementation (CASE-744)
**Decided:** 2026-07-22, Peter + BE-YAC-20260721-230521

## What this is

`libs/wip-archive` is a Python library — a sibling of `wip-auth` — that owns
the backup archive format contract and the fresh-restore id-remap pass.
WIP-Toolkit remains the operator CLI, as a consumer of the library.

The boundary follows the import graph, not the directory history: the
document-store restore engine imports exactly three toolkit modules
in-process (`archive.py`, `import_/remap.py`, `models.py`), and everything
else in the toolkit is CLI-side with zero in-process consumers. Code the
platform engine executes belongs in `libs/`, where its tests, deployment
handling, and review attention match its blast radius; code an operator
invokes belongs in a tool.

## The library

```
libs/wip-archive/
├── pyproject.toml          # hatchling, like wip-auth; version-stamped but
│                           # consumed as a path dep — no tarball ceremony
├── README.md
├── src/wip_archive/
│   ├── __init__.py
│   ├── archive.py          # ArchiveReader, ArchiveWriter, ENTITY_FILES —
│   │                       #   the .zip format contract, BOTH directions
│   ├── models.py           # Manifest, EntityCounts, NamespaceConfig,
│   │                       #   NamespaceEntry, ProgressEvent, …
│   └── remap.py            # IDRemapper (was import_/remap.py — flattened)
└── tests/                  # test_archive.py, test_models.py, test_remap.py
                            #   (moved with their modules from WIP-Toolkit/tests)
```

- **Three modules, no more.** `models.py` moves because `archive.py` imports
  `Manifest` from it and the engine consumes five of its models directly;
  internal dependencies stay clean (`models` ← `archive`; `remap` is
  dependency-free). Nothing else in the toolkit is engine-reachable.
- **Consumption model: path dependency, like wip-auth.** `-e
  ../../libs/wip-archive` in requirements, bind-mounted in dev, baked into
  release images. No vendored tarball, no four-edit bump — the library has
  no consumers outside this repo's services and the toolkit.
- **A `wip-test.sh` component** (`wip-archive`), like `wip-auth`.

## The toolkit after the split

WIP-Toolkit stays a package and keeps shipping its wheel into scaffolded app
projects (`create-app-project.sh`), which then also receive the wip-archive
wheel as its dependency. Surface after the split:

- **Commands kept:** `export`, `inspect`, `backfill-synonyms`,
  `update-document`, `seed`, `status` — thin consumers of platform APIs and
  of `wip_archive` (exporter and inspect read/write archives through the
  library).
- **Command deleted: `import`.** The platform has one import write-path —
  the server-side restore engine (`start_restore`). The client-side importer
  (`import_/fresh.py`, `import_/restore.py`, `import_/importer.py`) is
  deleted with its command and its tests. An operator moving an archive into
  a remote instance uploads it to that instance's restore endpoint; a second,
  client-side write-path would have to track platform validation forever and
  can silently disagree with it.
- Modules kept: `cli.py`, `client.py`, `config.py`, `export/`,
  `backfill.py`, `seed.py`, `status.py`, `convert_archive.py`,
  `_progress.py`. Whether `seed`/`status`/`backfill` later fold into
  `scripts/` is deliberately out of scope here — it needs usage data, and
  the split does not depend on it.
- The CASE-670 four-service integration harness
  (`WIP-Toolkit/tests/integration/`) stays with the toolkit: it exercises
  CLI export against the platform engine, which is exactly the toolkit's
  seam after the split.

## Consumer repoints

| Surface | Change |
|---|---|
| `document-store/requirements.txt` | `-e ../../WIP-Toolkit` → `-e ../../libs/wip-archive` |
| `backup_engine.py:25-33` | `wip_toolkit.archive` → `wip_archive.archive`; `wip_toolkit.import_.remap` → `wip_archive.remap`; `wip_toolkit.models` → `wip_archive.models` |
| `backup_service.py:32,322` | same repoints |
| `api/backup.py` GUARDRAIL 1 | carries over verbatim against the new name: no `wip_archive` imports in the API layer; grep must stay clean |
| `deployer/renderers/dev_simple.py` | the WIP-Toolkit special-case (`TOOLKIT_SERVICES`, dev-bake COPY, `_copy_tree_into`) repoints to `libs/wip-archive`, mounted like `wip-auth` |
| `scripts/build-release.sh:222-277` | needs-wip-toolkit branch repoints to wip-archive; service images stop containing the toolkit entirely |
| `scripts/wip-test.sh` | `wip-archive` added as a component; `wip-toolkit` stays (CLI + integration tests) |
| `WIP-Toolkit/pyproject.toml` | gains a `wip-archive` dependency; `create-app-project.sh` ships both wheels |
| Toolkit internals | `export/exporter.py`, `convert_archive.py`, `cli.py` (inspect) repoint their archive imports to `wip_archive` |

**No compatibility shim.** Every in-repo consumer is enumerated above; the
break lands in one refactor with all suites green. The one external surface
is the toolkit wheel already shipped to app projects: its **CLI commands are
unchanged**, but `wip_toolkit.archive`/`wip_toolkit.models` stop being
importable as library paths. Apps were never pointed at those imports by any
scaffold or doc; if one turns up using them, it re-vendors both wheels and
repoints — flagged in the CASE-744 thread rather than carried as a
permanent shim.

## Migration plan (2–3 commits)

1. **The move** — create `libs/wip-archive` (files moved with `git mv` so
   history follows), repoint engine + toolkit imports, requirements,
   `wip-test.sh`. Gate: `wip-archive`, `wip-toolkit`, and `document-store`
   suites green; a dev-stack restore replay (the kb+library archive) as the
   live path check.
2. **Deployment surfaces** — deployer renderer + `build-release.sh` +
   `create-app-project.sh`. Gate: deployer suite green; `wip-deploy install
   --target dev` renders and the restored stack passes `/wip-status`.
3. **The deletion** — `import` command, `import_/` client-side importer
   modules, their tests; docs that referenced `wip-toolkit import` repoint
   to the restore API. May fold into commit 1 if the diff stays readable.

## Out of scope

- Folding remaining CLI commands into `scripts/` (needs usage data).
- Any change to the archive format itself.
- The `versioned`-tarball consumption model (adopt only if an out-of-repo
  consumer ever appears).
