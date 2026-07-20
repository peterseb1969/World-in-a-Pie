# Backup & restore — the ritual, the verification, and the sharp edges

The operational contract for restoring a WIP instance. Written after a real
incident chain (July 2026): a restore into a kept PostgreSQL volume produced a
**silently empty reporting layer** — every doc-type sync failed on
schema-drifted bookkeeping tables while stale pre-existing copies of the same
tables shadowed the real data, and the diagnosis took hours. Everything below
exists so that class of failure is either impossible or loud.

---

## The ritual: a full restore wipes BOTH volumes

MongoDB is the source of truth; PostgreSQL is a derived reporting layer. A
restore therefore replays documents into Mongo and lets reporting-sync
repopulate postgres. The rule that makes this safe:

> **When you wipe, wipe together.** A full restore starts from an empty Mongo
> AND an empty postgres. Restoring into a kept postgres volume is exactly the
> incident trigger: tables created by older code versions keep their old
> shape (`CREATE TABLE IF NOT EXISTS` silently no-ops on them), and data
> synced under previous schema layouts survives as authoritative-looking
> fossils that shadow the freshly restored namespaces.

The clean sequence on a compose/dev install:

```bash
wip-deploy nuke --name <install> --remove-data --yes   # keeps secrets/
wip-deploy redeploy --name <install>                    # fresh empty volumes, bootstrap re-runs
# then restore the archive (Console, MCP start_restore, or REST)
```

Secrets under `~/.wip-deploy/<install>/secrets/` deliberately survive the
nuke — the same API keys and Dex passwords work after the cycle. (Caution
from a second real incident: `nuke` scopes by compose project, but WIP
containers use fixed names — with **two installs on one machine**, prune the
dormant one with plain `rm -rf ~/.wip-deploy/<name>`, never `nuke`.)

On k8s, deleting the namespaces' PVCs (or the namespaces themselves) plays
the same role; restore into absent namespaces only.

**Namespace deletion does not substitute for the postgres wipe.** Deleting a
namespace drops its postgres schema, but the global sync bookkeeping tables
are not namespace-owned and survive — with whatever shape the code that
created them had. Only the volume wipe (or an explicit migration) renews them.

---

## What the restore verifies for you

Restore is not blind anymore. The engine runs three verification phases per
namespace, backed by reporting-sync's parity check:

(This section describes `mode=restore`, the wholesale restore into an empty
target. For merging an archive into a namespace that already holds data, see
the next section — the preconditions differ.)

1. **Precondition (before anything is written):** the target namespace must
   be empty in Mongo AND its reporting schema must be absent/empty. Stale
   reporting tables fail the job with a message naming the remedy:

   ```
   Re-run with drop_stale_reporting=true to drop it, or clear it manually.
   ```

   `drop_stale_reporting` (REST form field on restore start; parameter on the
   MCP `start_restore` tool) is the explicit opt-in that drops the stale
   schema first. Unusable bookkeeping tables (pre-namespace-keying shape)
   also fail here — loudly, before any write, instead of as silent per-type
   sync failures afterwards.

2. **Structural gate (after templates, before documents):** reporting tables
   for every restored sync-enabled template must materialize with the right
   columns in the right schema. A persistent failure **halts the restore
   before any document moves** — the incident class is caught at the first
   template.

3. **Count parity (after documents):** expected row counts (computed from
   Mongo with the same query the sync uses) vs actual postgres rows, polled
   with a bounded wait. A mismatch **completes the restore with a warning**
   on the job record (`warnings` field of the job snapshot) — the data is
   safe in Mongo; the warning tells you the reporting layer needs a look.

If reporting-sync is not deployed (core preset), the phases degrade to a
logged warning — a restore never fails because the optional reporting layer
is absent.

Whatever the mode, the restore also rebuilds the Registry's **composite-key
claims** for the entries it wrote. Claims are derived state (one row per
namespace + entity type + composite-key hash, naming the entry that owns it),
so they never travel in an archive — but a namespace whose entries have no
claims has lost its uniqueness gate, and the next registration reusing one of
those keys would mint a second entity for one identity. A key already claimed
by a *different* entry is left with its incumbent and reported as a warning.

---

## Merging an archive into a live namespace

`mode=merge` (REST form field, MCP `start_restore`, `@wip/client`
`startRestore`) treats the archive as a **delta** against a namespace that
already holds data, instead of requiring an empty target. Same install, same
namespace name, IDs preserved. Use it to bring a namespace up to an archive's
state without tearing it down.

The preconditions invert accordingly: the namespace must **exist**, its
reporting schema is expected to hold tables (so `drop_stale_reporting` is
rejected — it would discard the data you are merging into), and only unusable
bookkeeping still refuses. The archive's namespace config is compared and any
drift reported, never applied: the live namespace's configuration belongs to
whoever runs it.

Entities the target lacks are inserted. Everything else is policy:

| Option | Applies to | Values |
|---|---|---|
| `on_clash` | documents | `skip` (default) — target wins. `overwrite` — the archive's LATEST version is appended on top of the target's head, adopting the target's `document_id`. |
| `on_schema_clash` | terminologies, terms, templates | `fail` (default) — refuse and report the differences. `skip` — target's schema wins. `upsert` — take the archive's. |

Two deliberate shapes worth knowing before you choose:

- **Overwrite never splices histories.** The target's versions are kept and the
  archive's are not interleaved into them — two independent version chains have
  no defined order, and merging them would corrupt the `(document_id, version)`
  contract. You get the target's history plus one new head. On a
  `versioned: false` template the single version is replaced in place instead,
  which is that template's own lifecycle.
- **`fail` is the schema default on purpose.** The platform's write path treats
  create as upsert, but a restore action's clash handling is the operator's
  decision, not the platform's. Merging data into a namespace whose schema has
  diverged from the archive is a migration someone should look at. Under
  `upsert`, a template lands as a NEW version (nothing overwritten);
  terminologies and terms have no version axis, so there `upsert` updates the
  row in place.

A merge **refuses outright** when the archive and target disagree about
identity — the same ID under a different logical key, or one logical key under
two IDs. Within one install that means identity has been corrupted, and merge
preserves IDs so it cannot reconcile it.

### Merging an archive from a different install

Set `cross_install=true` when the archive comes from another install. The two
sides never shared an ID space, so the same real-world entity legitimately
exists under two different UUIDs — and the refusal above would fire on data
that is perfectly healthy. With the flag, that case becomes a **match**: the
target's copy wins, the incoming one is dropped, and every incoming reference
to it is rewritten to the target's ID.

This is the consolidation case — folding two installs' copies of one namespace
together. It is never inferred, and cannot be: the same evidence means
corruption within one install and ordinary divergence across two. Only the
caller knows which they have.

Two things follow that are worth knowing:

- **Skipping is only half the job.** If the target's `GENDER` wins, the
  incoming terms still carry the *other* install's `terminology_id`. They are
  repointed before insert — otherwise the merge would import references that
  resolve to nothing. The same applies one level down to Registry composite
  keys, which embed parent IDs and are rehashed after rewriting.
- **Matching runs in dependency order for a reason.** A term's logical key is
  `(terminology_id, value)`, so the term can only be recognised as one the
  target already has *after* its terminology has contributed its match. The
  dry run reports how many identities were matched and how many incoming
  entities had references rewritten.

`on_schema_clash=fail` still applies, and matters more here: consolidating two
installs whose template versions have diverged would otherwise silently
validate one install's documents against the other's schema.

`dry_run` is exact for a merge: the plan is computed before anything is
written, so the report is what a real run would do — per entity type, how many
would be inserted, are unchanged, clash, or conflict, with the first few
differences named. It still fails on everything a real run would refuse, which
is the point of asking first.

```bash
# See what would happen, decide, then run it
start_restore(namespace="kb", archive_path="kb.zip", mode="merge", dry_run=True)
start_restore(namespace="kb", archive_path="kb.zip", mode="merge",
              on_clash="overwrite", on_schema_clash="skip")
```

---

## The one-call diagnosis: the parity verb

Any time the reporting layer "looks empty" or you doubt postgres reflects
Mongo, ask it directly — no psql spelunking:

```bash
# MCP tool
check_reporting_parity(namespace="kb")

# REST
GET /api/reporting-sync/parity?namespace=kb[&include_counts=false]
```

Per sync-enabled template it reports: table present in the namespace's
schema, missing columns (against what the sync itself would build),
bookkeeping row, and expected-vs-actual counts. Namespace-level it reports
schema presence, table count, and whether the bookkeeping tables are usable
at all — a database predating the namespace-keyed bookkeeping shape is named
explicitly, with remediation. `include_counts=false` is the cheap structural
form.

Interpreting it: structural issues → tables missing/mis-shaped (was the
restore's phase-1 skipped or the sync broken?); count mismatches → sync
behind or blocked (check `get_sync_status`, re-run the batch sync);
`bookkeeping_tables_ok: false` → old-shape database — wipe per the ritual,
or apply the namespace-column ALTER by hand.

---

## Sharp edge: runtime API keys DO NOT survive the ritual

Two kinds of API keys exist, and the ritual treats them very differently:

- **Deployment secrets** (`~/.wip-deploy/<install>/secrets/`: the master
  api-key, Dex passwords, postgres/minio credentials) — survive nukes and
  redeploys by design. Nothing to do.
- **Runtime keys** — anything created via `POST /api/registry/api-keys` (the
  Console UI or MCP `create_api_key`), including every namespace-scoped
  least-privilege key — live **only in MongoDB**, together with the
  `NamespaceGrant` documents that carry their write permissions. **Backups
  exclude both.** A wipe-restore cycle silently destroys every runtime key
  and every grant, and they cannot be re-registered with the same plaintext
  (the server generates key material and returns it exactly once).

Until keys can be declared in the deploy spec (planned deployer work), plan
every wipe-restore with a key-re-mint step afterward: recreate the runtime
keys, re-grant their scopes, and redistribute the new plaintexts to whatever
holds them (client configs, `*-api-key` files, app env).

---

## Quick checklist

```
[ ] Backup taken and verified downloadable (list_backup_jobs / download)
[ ] Inventory of runtime API keys + grants that will need re-minting
[ ] Mongo AND postgres volumes wiped together (or target namespaces + schemas absent)
[ ] Restore run; drop_stale_reporting only after understanding what it drops
[ ] Job completed — check the job record's `warnings` list, not just its status
[ ] check_reporting_parity green for every restored namespace
[ ] Runtime keys re-minted, grants re-asserted, plaintexts redistributed
```
