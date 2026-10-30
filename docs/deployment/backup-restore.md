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

**The verdict is on the job.** A completed restore or merge carries
`result.reporting_verification` on its job snapshot, so a script can tell a
verified restore from one that gave up without reading `warnings`:

```json
"reporting_verification": {
  "status": "incomplete",
  "namespaces": {
    "kb": {
      "status": "incomplete",
      "reason": "no reporting rows written for 60s",
      "gaps": [
        {"kind": "table", "name": "term_relations",
         "issue": "term_relations_mismatch", "expected": 7, "actual": 0}
      ]
    }
  }
}
```

Each namespace the run wrote gets one of:
- `verified`: count parity held.
- `incomplete`: the wait gave up. `gaps` lists each gap: `kind` (`template`,
  `table`, `view`), `name`, `issue` and, where the check has them, the
  expected and actual counts.
- `unavailable`: reporting-sync could not be reached.
- `not_checked`: the run did not verify reporting. This is the case when
  documents were skipped, and for a fresh restore (`namespace_map`), which
  does not wait for reporting: its sync starts when the job completes.

`status` at the top is the worst of the namespaces' verdicts. The standing
check (below) repairs most gaps afterwards; confirm with the parity check.

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
already holds data, instead of requiring an empty target. Use it to bring a
namespace up to an archive's state without tearing it down, or to fold another
install's copy of a namespace into this one.

A merge runs in two passes.

### Pass 1 — definitions must be compatible

Before a single document moves, the archive's terminologies, terms and
templates are checked against the target's, **by content, not by ID**. That
matters: two installs that independently created `GENDER` hold it under
different UUIDs, and comparing content recognises them as the same
vocabulary without you having to say where the archive came from. The same
pass learns which definitions are the same thing under different IDs, and the
document pass uses that mapping to repoint incoming data.

Compatible means, per definition:

- **Template** — the target has one with the same value and identical content,
  or has none with that value.
- **Terminology** — the target has none with that value, or has one whose
  terms reconcile.
- **Terms** — extra terms on the archive side can be added; extra terms on the
  *target* side are harmless, since nothing incoming references them.

Incompatible is the leftover: **same name, different content**. A template
with the same value and a different schema refuses the merge, because merging
documents under it would validate one side's data against the other's
contract.

**The default changes nothing.** Verify-only is deliberate: altering a live
namespace's definitions is an active decision, never a side effect of
restoring data into it. Two separate opt-ins let the merge extend them:

| Flag | Effect |
|---|---|
| `add_missing` | Insert terminologies and templates the target does not have |
| `extend_terminologies` | Add terms the target's terminology is missing |

They are separate because allowing new vocabulary entries is not the same
decision as allowing new schemas.

Where a definition matches but its **label, description or aliases differ**,
the target's win — and the difference is reported, in the job output and the
dry run. Losing a label silently is the kind of change you otherwise discover
much later, from a UI that no longer says what you expect.

### Pass 2 — documents

With a schema both sides agree on, documents merge per identity under
`on_clash`:

- `skip` (default) — target wins; the archive's version is not imported.
- `overwrite` — the archive's **latest** version is appended on top of the
  target's head, adopting the target's `document_id`.
- `newer` — the same write, but only where the archive's copy has a more
  recent `updated_at`.

`newer` compares when the content last *changed*, not when the document was
created: a document created in January and edited yesterday should beat one
created in June and never touched, and the `document_id`'s embedded UUID7
time would get that backwards. A tie keeps the target — equal timestamps say
nothing about which side to prefer, and writing on no information is worse
than leaving a live namespace alone. A missing or unparseable timestamp on
either side also keeps the target, and is reported as a job warning rather
than passing silently.

> Merging between two installs, `newer` is only as reliable as the two
> machines' clocks. UTC removes timezone error, not skew.

Overwrite never splices histories. The target's versions are kept and the
archive's are not interleaved into them — two independent version chains have
no defined order, and merging them would corrupt the `(document_id, version)`
contract. You get the target's history plus one new head. On a
`versioned: false` template the single version is replaced in place instead,
which is that template's own lifecycle.

`older_versions` decides what happens to the target's versions under the new
head:

- `retire` (default) — they are soft-deleted (`inactive`), exactly as a
  normal write retires the version it supersedes. The document has one active
  version.
- `keep` — they stay `active` beside the new head. A document with several
  active versions is legal, like several active template versions, but it is a
  choice: every query by `status=active` returns them all. Reporting still
  holds one row per document (the latest version), and the parity check counts
  documents, not versions, so it passes either way.

A merge refuses outright on one thing no policy covers: **one ID naming two
different entities** across the two sides. Matching is by content, so the same
entity under two IDs is a match; the reverse cannot be resolved, since picking
either side would destroy an identity.

### Preconditions and the dry run

The namespace must **exist**, its reporting schema is expected to hold tables
(so `drop_stale_reporting` is rejected — it would discard the data you are
merging into), and only unusable bookkeeping still refuses. The archive's
namespace config is compared and any drift reported, never applied: the live
namespace's configuration belongs to whoever runs it.

`dry_run` is exact: both passes are computed before anything is written, so
the report is what a real run would do — per type, how many definitions are
already identical, how many would be added, how many map to the target's IDs,
every difference the target won, and the document counts. It still fails on
everything a real run would refuse.

```bash
# See what would happen, decide, then run it
start_restore(namespace="kb", archive_path="kb.zip", mode="merge", dry_run=True)
start_restore(namespace="kb", archive_path="kb.zip", mode="merge",
              add_missing=True, on_clash="overwrite")
```

The same from the command line:

```bash
wip-toolkit restore kb.zip --mode merge --dry-run
wip-toolkit restore kb.zip --mode merge --add-missing --on-clash overwrite \
    --older-versions retire
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

It also covers what reporting derives besides document tables. Every view
registered for the namespace must exist (`views`). With counts, the term
tables are checked (`derived_tables`): active `terms` and `term_relations`
rows against def-store's active totals, `term_names` rows for every term that
has names, and `term_closure`'s reflexive rows against what its rebuild
writes. Each failure is counted in `derived_issues` and named by an issue
code (`terms_mismatch`, `term_relations_mismatch`, `term_names_missing`,
`term_closure_stale`, `check_failed`). `ok` is true only when all of it
passes, so a namespace whose terms are back but whose relations or closure
are empty no longer reads as verified.

## The standing check: reporting repairs itself

The restore checks parity once, within a time limit. A gap that outlives that
window, or appears later (a missed event, views lost in a reporting rebuild),
is caught by reporting-sync itself. Every 300 seconds it runs the parity check
over every active namespace, and repairs what it finds with the narrowest sync
that closes the gap:

| Gap | Repair |
|---|---|
| a template's table missing or short | that template's batch sync |
| `terms_mismatch`, `term_names_missing` | the namespace's term sync |
| `term_relations_mismatch` | the namespace's term-relation sync (ends with a closure rebuild) |
| `term_closure_stale` | a closure rebuild |
| a registered view missing | the namespace's registered views re-applied |

- A gap is repaired only when two consecutive passes see it, so the lag of
  normal event sync is never mistaken for one.
- A namespace with a batch sync running is skipped for that pass; the
  running sync owns it.
- Each gap gets at most 3 repairs. One that persists past them is logged once
  at error level (`not healed after 3 repair(s)`) and left alone.
- Some gaps are never repaired automatically: a legacy pre-split table,
  old-shape bookkeeping tables, a check that could not run. They are logged
  once at error level as needing an operator.

`GET /api/reporting-sync/parity/status` (admin or service key) shows the last
pass per namespace (its gaps, the repairs made per gap, `not_healing`) and the
recent repairs. The interval and the repair limit are the service's
`PARITY_CHECK_INTERVAL_SECONDS` (0 turns the check off) and
`PARITY_REPAIR_ATTEMPTS`; wip-deploy does not set them, so every install runs
the defaults.

Interpreting it: structural issues → tables missing/mis-shaped (was the
restore's phase-1 skipped or the sync broken?); count mismatches → sync
behind or blocked (check `get_sync_status`, re-run the batch sync);
`bookkeeping_tables_ok: false` → old-shape database — wipe per the ritual,
or apply the namespace-column ALTER by hand.

Note that the definitions tables (`terminologies`, `templates`, `terms`,
`term_relations`) are batch-synced additively — upsert only, no deletes — so
a row orphaned by a missed hard-delete event survives every batch re-run.
The remediation is a schema-level rebuild, not hand-psql: reporting-sync's
`DELETE /namespace/{prefix}` (atomic `DROP SCHEMA CASCADE`) followed by
`POST /sync/batch` for the namespace, whose definitions companion rebuilds
all four tables alongside the document tables.

---

## Sharp edge: runtime API keys DO NOT survive the ritual

Two kinds of API keys exist, and the ritual treats them very differently:

- **Deployment secrets** (`~/.wip-deploy/<install>/secrets/`: the master
  api-key, Dex passwords, postgres/garage credentials) — survive nukes and
  redeploys by design. Nothing to do.
- **Runtime keys** — anything created via `POST /api/registry/api-keys` (the
  Console UI or MCP `create_api_key`), including every namespace-scoped
  least-privilege key — live **only in MongoDB**, together with the
  `NamespaceGrant` documents that carry their write permissions. **Backups
  exclude both.** A wipe-restore cycle silently destroys every runtime key
  and every grant, and they cannot be re-registered with the same plaintext
  (the server generates key material and returns it exactly once).

Keys declared in the deploy spec (`--api-key` / `--api-keys-file`, see
`docs/wip-guide.md` §4.3) are deployment secrets and survive. Every runtime
key does not: plan every wipe-restore with a key-re-mint step afterward.
Recreate the runtime keys, re-grant their scopes, and redistribute the new
plaintexts to whatever holds them (client configs, `*-api-key` files, app
env).

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
