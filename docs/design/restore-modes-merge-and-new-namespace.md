# Restore Modes

How an archive gets back into a WIP instance. One upload surface, three modes
that differ in what they preserve and what they refuse:

| Mode | Identity | Target precondition | Use case |
|---|---|---|---|
| `restore` | Preserved — every canonical id kept | Every archived namespace empty | Disaster recovery on the same install |
| `merge` | Preserved — archive as a delta | Target holds live data | Reconciling a fork, importing from another install |
| `fresh` | Re-minted — nothing kept | Every target empty or absent | A live copy BESIDE the original; cross-install cloning |

All three run in document-store's `DirectRestoreEngine`
(`components/document-store/src/document_store/services/backup_engine.py`),
reached via `POST /backup/namespaces/{namespace}/restore` (multipart upload,
streamed to disk), the MCP `start_restore` tool, and
`@wip/client.startRestore`. Restore and merge are also reachable from a
stored backup job; fresh is upload-only. `dry_run` is real for every mode:
preconditions run, the plan is computed and reported, nothing is written —
and the plan survives on the job record (`result`), not just in the progress
stream.

## Shared machinery

**Archive format (v3).** A zip with `manifest.json` and a
`namespaces/<prefix>/` subtree per namespace (`terminologies.jsonl`,
`terms.jsonl`, `term_relations.jsonl`, `templates.jsonl`, `documents.jsonl`,
`files.jsonl`, `registry_entries.jsonl`), plus flat `blobs/<file_id>`
(namespace-agnostic, globally-unique ids). `ArchiveReader`
(`libs/wip-archive/src/wip_archive/archive.py` — moved out of WIP-Toolkit into the
shared `wip-archive` lib by CASE-744) streams entities row-by-row —
whole-member decoding is banned; it took peak RSS from 386 MB to 31 MB on a
90 MB member. An archive may carry several namespaces; the manifest lists
each with its `namespace_config` and per-type counts.

**The Registry is the identity authority.** No mode mints an id locally.
Fresh provisions ids from the Registry per the TARGET namespace's
`id_config`; restore and merge re-register the archived ids. Composite keys
must match what each owning service would have registered — the shapes live
in `remap_restore.composite_key_for` and cite their source clients, because
a wrong-shaped key does not fail: it silently creates an identity nothing
will ever match.

**Reporting is decoupled.** A restore's job is to get the data in; no
reporting sync runs mid-restore. On completion the engine fire-and-forgets a
namespace-scoped batch sync (`POST /sync/batch?namespace=<target>` — query
parameter, not body) and auto-triggers a namespace validation job
(reference + identity-hash integrity, `validation_job_ids` on the job
record). Verify with `check_reporting_parity` any time.

## Mode: restore

Insert the archive wholesale, preserving every id. Every namespace the
archive carries must be empty on the install; admin permission is required
on each. A stale reporting schema for a target refuses the restore unless
`drop_stale_reporting=true` (a dry run reports the would-drop only).
Composite-key claims are rebuilt from the restored registry entries — a
restore is not just rows, it re-establishes the Registry's dedup state.

## Mode: merge

The archive is a delta against a namespace that already holds data. Two
passes, because documents cannot be merged under definitions the two sides
disagree on.

**Pass 1 — definitions, matched by content.** Terminologies, terms and
templates are compared by content, not id, so an archive from another
install works with no extra parameter; the pass also learns which
definitions are the same thing under different ids, producing the id
mapping pass 2 rewrites with. Incompatible definitions refuse the merge.
Changing the target's definitions is opt-in and split deliberately:
`add_missing` inserts terminologies and templates the target lacks;
`extend_terminologies` adds missing terms. Where a definition matches but
label/aliases differ, the target's win and the difference is reported.

**Pass 2 — documents, under `on_clash`.** `skip` (default) keeps the
target's version of a clashing identity. `overwrite` appends the archive's
latest version on top of the target's head, preserving both histories (on a
`versioned:false` template it replaces the single version in place).
`newer` does what `overwrite` does only where the archive's copy has a more
recent `updated_at`; ties and missing/unparseable timestamps keep the
target's and are reported as job warnings — across installs `newer` is only
as reliable as the two machines' clocks.

**Different-namespace target.** A merge may write into a namespace other
than the archive's: identities are rehashed for the target's composite-key
scope, and a free-ids pre-check plus a pre-write reference check run first —
a merge lands in a live namespace that cannot be deleted to recover, so it
verifies before it writes. Clash policies refuse at the API surface when
sent with a plain restore (nothing can clash in an empty target; silently
accepting the option would misreport what ran).

**Bootstrap-record collisions between app-derived namespaces.** Scaffolded
apps historically minted their bootstrap provenance template under one
shared value, `BOOTSTRAP_RECORD`, with per-app shapes that drift — so any
merge of two app-derived archives refused at pass 1 on that one template.
The scaffold now namespace-prefixes the value (`KB_BOOTSTRAP_RECORD`,
`CT_BOOTSTRAP_RECORD`, …), which prevents the collision for newly
bootstrapped namespaces and makes a merged-in record self-labeling: a
prefixed bootstrap record inside a merged namespace announces which
namespace's bootstrap it documents. For EXISTING namespaces that still
carry the unprefixed value, there is deliberately no drop parameter and no
platform special-case — the remediation is an operational recipe made of
first-class operations, self-expiring once the last unprefixed pair is
retired. Either variant works; both rely on the definitions pass comparing
per (value, version) over the archive's entities only, and on the default
export excluding inactive template versions:

1. *Retire the source's record*: deactivate the source namespace's
   bootstrap template and archive its record(s), re-export, merge. The
   target keeps its own provenance; the source's stays recoverable in the
   source (soft states only). If the source must stay pristine, reactivate
   after the export.
2. *Converge*: update BOTH sides' templates from one identical payload
   (version slots must align), `migrate` the source's records to the
   converged version (dry-run first), deactivate the source's old version,
   re-export, merge — both provenance trails survive the merge as
   documents of the shared shape.

## Mode: fresh

Nothing is kept. Every terminology, term, template, document and file is
registered anew with a Registry-minted id, and every reference between them
is rewritten. That is what lets a namespace be restored beside the one it
came from — two live copies cannot share a canonical id.

**Where new identities go.** A single-namespace archive takes
`target_namespace`. A multi-namespace archive takes `namespace_map` — an
explicit `{source: target}` covering EVERY namespace it carries. There is no
implicit default: an unmapped namespace restored to its old name would
collide with the live original, which is the accident this mode exists to
avoid. A target may equal its source name only when that namespace is empty
or absent (the per-target emptiness check enforces it). Several sources may
collapse into one target (N:1). Admin permission is checked per target.

**N:1 collisions refuse at plan time.** The Registry's composite key is an
upsert: two sources claiming the same key (e.g. the same template value)
would not fail — the second claim silently RESOLVES to the first entity,
merging definitions that may disagree. `RemapCollisionError` lists the
colliding keys instead; merging same-keyed content is merge-mode's job.
Identity-less documents are exempt (an empty key opts out of dedup by
design). For an N:1 target's namespace config, the first mapped source
(archive order) wins; `allowed_external_refs` naming other archive sources
follow the map, names outside the archive are kept.

**Type-major planning, one shared remapper.** Planning
(`remap_restore.plan_multi`) walks entity types in dependency order
(terminologies → terms → templates → files → documents); for each type,
EVERY source is provisioned and its old→new mapping fed into one shared
`IDRemapper` before ANY source's rows are rewritten. Source-major ordering
would rewrite namespace A's documents while namespace B's entities still
map to nothing, leaving cross-namespace references on old ids. Registry
keys build from a GLOBAL old→new map for the same reason: a document may be
pinned to another archived namespace's template (legal on the live create
path — by UUID or qualified `ns:VALUE`; bare values resolve own-namespace
only), and its key must embed that template's NEW id, which its own
source's plan never learns. One identity is provisioned per ENTITY, not per
row — a versioned type's rows share their composite key, so per-row
provisioning collides on the second version.

**Fresh means fresh.** After a fresh restore NO data points to anything
from the original namespaces; history lives in the archive, not in the
restored rows. Beyond ids, this covers reference snapshots: the
denormalized `resolved.namespace` follows the source→target map (a
namespace outside the archive passes through — that entity was not
re-minted, so its snapshot still describes it), and a `lookup_value` that
is a canonical id follows its entity through the id maps. Human-readable
lookup values stay: namespace-agnostic text that resolves in the new
context. The regression test sweeps the serialized restored row for any
old id or source-namespace string, so a future snapshot field that leaks
provenance fails the suite by construction.

**Identity hashes.** A document's identity hash is namespace-free and
value-based, so it survives re-minting — unless an identity field itself
holds a canonical id that just changed; those documents are rehashed and
counted in the plan (`rehashed_documents`).

**Crash behaviour: reserved, then one activation.** Provisioned entries are
*reserved*, and reserved entries do not resolve. A job that dies partway
leaves an invisible, reconcilable namespace rather than a half-live one;
one activation step at the end makes the whole set visible at once. The
flip side: a failed remap leaves reserved identities holding their
composite keys, so retrying into the same target collides — use a fresh
target or reconcile first; there is no automatic cleanup. Provisioning is
chunked at 1,000 keys per Registry call, and target namespaces are created
(upserted) BEFORE provisioning — the Registry mints per the target's
`id_config` and 404s for a namespace it does not know. A dry run cannot
catch that ordering: its stand-in provisioner never asks the Registry
anything.

**Result shape.** `result.namespace_map` carries the mapping;
`result.planned` is keyed per source namespace with per-type counts;
`source_namespace`/`target_namespace` remain for single-namespace callers.

## Measured performance

From live runs against localhost (Apple silicon, podman), 2.25 GB clintrial
archive — 252,586 document rows, 235,162 identities, 232,051 synced docs.
Post CASE-740/741 (bulk activation, batch-sync throughput):

- Fresh restore engine total: **3 min 6 s** (was 5 min 58 s pre-fix).
  Breakdown: provision + definitions + documents + file rows ~2 min 30 s;
  570 blobs ~21 s; activation of 235k identities **~8 s** (471 bulk
  activate calls = ⌈235,162/500⌉ at ~65 calls/s — was ~3 min).
- Post-restore namespace-scoped batch sync: **1 min 52 s** (was 13 min 8 s).
  The largest template (156,561 docs) runs at ~1,395 docs/s; its job
  instrumentation reads fetch_ms=70,605 / upsert_ms=40,468 / sibling_ms=2 —
  fetch dominates at ~450 ms per 1,000-doc page, so cursor pagination on
  the document fetch is the known next lever.
- End to end, upload trigger → SQL-readable: **~5 min 30 s** (was ~19 min).
- Restore scales linearly in rows (65,923 rows took 93 s pre-fix at the
  same per-row cost as the 252k run).
- Provisioning emits no progress events (~40 s per 61k identities looks
  stalled at `phase_validate`) — known gap.

## Surfaces

- Route: `POST /api/document-store/backup/namespaces/{ns}/restore`
  (multipart; form fields `mode`, `target_namespace`, `namespace_map`
  (JSON object), `on_clash`, `add_missing`, `extend_terminologies`,
  `skip_documents`, `skip_files`, `batch_size`, `dry_run`,
  `drop_stale_reporting`). Retired toolkit-era params
  (`register_synonyms`, `continue_on_error`) are rejected with 400.
- MCP: `start_restore` (same parameters; `namespace_map` as a dict).
- `@wip/client`: `startRestore(namespace, archive, options)` with
  `RestoreOptions.namespace_map: Record<string, string>` (since 0.45.0).
- Progress: `GET /backup/jobs/{id}` snapshots, `/events` SSE stream,
  `/download` for archives; jobs carry `warnings`, `result`,
  `validation_job_ids`.

## Related

- `docs/uniqueness-and-identity.md` — identity ontology the modes rely on.
- `docs/design/template-identity-unification.md` — template create as
  upsert; why template_id is the stable handle across versions.
- CASE-740 / CASE-741 — activation and batch-sync throughput.
- CASE-744 — moving the archive/remap machinery out of WIP-Toolkit into a
  shared library.
