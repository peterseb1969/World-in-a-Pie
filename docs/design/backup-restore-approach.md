# Backup / Restore Implementation Approach

**Status:** Decision pending
**Author:** BE-YAC-20260408-1624
**Context:** CASE-23 v1.0 Phase 3 — REST backup/restore wrapper
**Decision needed before:** Document-Store STEP 5 (REST endpoints) is implemented

## Why this document exists

Work on CASE-23 started with an implicit assumption: the REST
backup/restore endpoints would wrap `wip-toolkit`'s existing
`run_export` / `run_import` functions. Three commits have already
landed against that assumption:

- `d71b3f6` — `run_export` gained `progress_callback` + `non_interactive`
- `11aa075` — `run_import` / `restore_import` / `fresh_import` gained the same
- `704a35d` — document-store added `wip-toolkit` as an editable dependency
- `88ed7f6` — `BackupJob` Beanie model
- `a94f3f7` — `backup_service.py` async/sync bridge around the toolkit

Reusing the toolkit was never evaluated against a fresh
implementation. This document turns that default into a decision by
laying out both paths concretely, then recommending one.

## Option A — Reuse wip-toolkit (current path)

The document-store REST endpoints wrap `run_export` / `run_import`.
The sync toolkit runs in a worker thread; progress events are bridged
to the event loop via `asyncio.Queue` + `loop.call_soon_threadsafe`;
job state is persisted to a `BackupJob` MongoDB document.

### What it costs today
- **Async/sync bridge** — `backup_service.py` (~220 LOC) exists solely
  because the toolkit is sync httpx. This is the code most likely to
  have subtle threading bugs and the hardest to reason about.
- **HTTP loopback for document-store's own data** — the toolkit talks
  to *all* WIP services over HTTP, including the process it is
  running inside. Reads and writes for documents, files, and term
  references round-trip through uvicorn's request stack instead of
  hitting Beanie directly.
- **CLI-isms leak into server context** — `click.confirm` + rich
  console output had to be guarded with `non_interactive=True`. The
  progress hook had to be bolted on (`_progress.py`, `emit()` helper).
  Future toolkit changes that assume a terminal will bleed through.
- **Large archives buffer to disk twice** — the toolkit streams to a
  local ZIP, then the endpoint streams the file back to the client.
  No opportunity for truly streaming `/backup` responses.

### What it gives us for free (~2900 LOC)

```
WIP-Toolkit/src/wip_toolkit/
├── export/exporter.py            508 LOC
├── import_/importer.py           173 LOC   (dispatcher)
├── import_/restore.py            888 LOC   (restore into existing namespace)
├── import_/fresh.py              916 LOC   (fresh import with ID remapping)
├── archive.py                    220 LOC   (zip read/write, manifest)
└── client.py                     198 LOC
                                ──────
                                 2903 LOC
```

Things that are already solved here and would otherwise need to be
re-solved:

- Manifest schema + versioning
- Archive layout and streaming read/write
- Dependency-aware ordering (terminologies → terms → relations →
  templates → template activation → files → documents → synonyms)
- Fresh mode: Registry ID remapping, synonym registration, template
  activation ordering
- Restore mode: skip-if-exists semantics, conflict resolution
- Relation re-creation after target terms exist
- File blob re-upload against a fresh object store
- Dry-run support
- `continue_on_error` semantics
- Per-entity failure accounting in `ImportStats`
- Manifest-driven health checks before destructive work

This is the load-bearing part of the trade-off. These are not
abstract features — each one represents an edge case that *will* be
re-discovered if we rewrite.

### What it also gives us
- A single implementation of backup/restore shared between the CLI
  and the REST surface. No drift risk.
- CLI users continue to benefit from every improvement we make to
  the toolkit (e.g. the progress_callback work is already useful for
  a richer CLI progress bar).

## Option B — Fresh server-first implementation

Build `components/document-store/src/document_store/backup/` as a
native async backup/restore subsystem. Access MongoDB directly via
Beanie; call Registry / Def-Store / Template-Store via their existing
async clients; stream archive contents via chunked `StreamingResponse`.

### What it costs
- **Re-implement the list above** — manifest, archive layout,
  dependency ordering, fresh vs restore modes, ID remapping,
  relations, template activation, file re-upload, dry-run,
  per-entity accounting. Estimate: ~1500–2000 LOC of new code plus an
  equivalent volume of tests. Most of it is not hard individually;
  the cost is discovering the edge cases `restore.py` already
  handles.
- **Two implementations of backup/restore** — the CLI still needs the
  toolkit (or has to become a thin client over the REST endpoints).
  If we keep the toolkit AND add a server implementation, they will
  drift. If we drop the toolkit and make the CLI a REST client, the
  CLI stops working against older/remote WIP deployments that don't
  expose the backup endpoints.
- **Restore path risk** — restore is the harder half. Getting FK
  ordering, synonym registration, and template activation wrong on
  restore corrupts a namespace silently. The toolkit has been through
  this already.

### What it gains
- **No async/sync bridge.** `backup_service.py` disappears.
- **No HTTP loopback for document-store's own data.** Document
  reads/writes hit Beanie directly, same as every other endpoint.
- **Streaming archive production.** Writes chunks to the response as
  they're produced, instead of buffering a full ZIP on disk first.
  Matters at multi-GB scale.
- **Server-first design throughout.** No flags to disable CLI
  behavior. Progress is part of the API from day one, not retrofitted.
- **Freedom to restructure the toolkit later.** The toolkit is no
  longer in the critical path for the REST surface, so changes to it
  don't risk breaking production backups.

## Option C — Hybrid (reuse now, rewrite later)

Ship CASE-23 v1.0 on Option A. Use the production feedback and the
concrete friction points to inform a v1.1 rewrite that drops the
toolkit dependency and makes the REST path native. The toolkit-side
progress_callback work is still useful for the CLI.

This is not strictly a third option — it is Option A with an
explicit "we will rewrite this" follow-up. It has the same
short-term cost as A and the same long-term cost as B, but defers
the rewrite until we know what actually hurts in production.

## Trade-off summary

| Concern | A: Reuse toolkit | B: Fresh rewrite | C: Hybrid |
|---|---|---|---|
| Time to ship v1.0 | Days (3 more commits) | Weeks | Days |
| Lines of new code committed | ~400 more | ~2000 more | ~400 now + ~2000 later |
| Async purity | Bridge required | Native | Bridge required |
| HTTP loopback for own data | Yes | No | Yes (v1.0), No (v1.1) |
| Edge cases re-discovered | Zero | Many | Zero (v1.0), some (v1.1) |
| Drift risk CLI vs REST | None | High | None (v1.0), managed (v1.1) |
| Risk to restore correctness | Low (battle-tested) | Medium | Low (v1.0), Medium (v1.1) |
| Archive streaming | No | Yes | No (v1.0), Yes (v1.1) |

## Recommendation

**Option A for v1.0, with a hard commitment to revisit in v1.1 once
the REST surface has production usage.**

Reasoning:

1. The load-bearing question is not "is the bridge ugly" but "can we
   re-implement restore correctly." `restore.py` is 888 LOC of edge
   cases we do not currently have an inventory of. Rewriting blind is
   a correctness risk that does not match CASE-23's scope ("wrap
   existing toolkit behind REST").
2. The bridge is ugly but it is contained — ~220 LOC in one file with
   good test coverage. If it causes production issues, we will find
   out fast and have evidence to justify the rewrite.
3. The toolkit-side work (`progress_callback` in both export and
   import) is a legitimate toolkit improvement regardless of which
   path we take. None of it is wasted.
4. CLI users benefit from toolkit improvements for free during v1.0.
   This is a real asset.
5. If we rewrite in v1.1, we will have a concrete requirements doc
   (driven by what actually broke in v1.0) instead of guessing at
   edge cases.

Conditions for revisiting earlier (before v1.1):

- The async/sync bridge produces a race condition in production
- The HTTP loopback materially impacts backup throughput at >10 GB
- A significant toolkit refactor becomes necessary for another reason
  (making the rewrite near-free)
- The CLI and REST paths diverge in user-visible ways

## Decision

**2026-04-08 — Peter decided Option A (reuse wip-toolkit) for v1.0**, conditional
on STEP 5 landing the three guardrails below that keep a v1.1 rewrite clean.

### Removal audit (how cheap v1 is to rip out if we rewrite)

Files that would be **deleted** on rewrite (~540 LOC, concentrated):

| File | LOC |
|---|---|
| `services/backup_service.py` | ~220 |
| `tests/test_backup_service.py` | ~260 |
| Loopback `WIPClient` config (STEP 4, not yet written) | ~30 |
| `-e ../../WIP-Toolkit` in `components/document-store/requirements.txt` | 1 line |
| `ToolkitRunner` closure inside `api/backup.py` (STEP 5) | ~30 |
| `init_beanie` line for `BackupJob` in `main.py` | stays |

Files that **survive any rewrite** (already toolkit-independent):

- `models/backup_job.py` — schema is strings + enums, zero toolkit types
- `api/backup.py` URL and request/response shapes — rewrite swaps internals
- `@wip/client` methods (STEP 7) — talks to REST, not to the toolkit
- MCP tools (STEP 8) — same
- Integration test (STEP 6) backup→restore round-trip — tests correctness
- Toolkit-side `progress_callback` work (`d71b3f6`, `11aa075`) — still valuable
  for CLI users

### The three guardrails (MANDATORY for STEP 5)

These are small, zero-cost in v1.0, and the entire reason v1 can be
cleanly removed. Whoever writes STEP 5 **must** enforce all three or
the rewrite path gets expensive.

**Guardrail 1 — Single import chokepoint**

`components/document-store/src/document_store/api/backup.py` must not
`import wip_toolkit` directly. It only imports from
`services.backup_service`. The toolkit runner closure that calls
`run_export` / `run_import` must live inside `api/backup.py` as a
local factory function that `backup_service.start_job` accepts as a
callable — or inside `backup_service.py` itself. Either way, the
import surface touching the toolkit is **one file**. Rewriting
becomes: delete that file, replace the closure.

Check during review: `grep -r "import wip_toolkit" components/document-store/src/document_store/api/`
must return zero hits.

**Guardrail 2 — SSE event envelope ≠ toolkit `ProgressEvent`**

The `on_event` hook inside `backup_service.py` currently forwards the
raw `wip_toolkit.models.ProgressEvent`. That type **must not leak to
the wire**. Define a dedicated envelope in
`models/backup_job.py` (suggested name: `BackupProgressMessage` or
reuse `BackupJobSnapshot`) derived from the `BackupJob` record's
fields (phase, percent, message, details, status). The SSE endpoint
serializes this envelope only. A future rewrite can produce the
same envelope from a non-toolkit source without the client noticing.

Check during review: `grep "ProgressEvent" components/document-store/src/document_store/api/backup.py`
must return zero hits. The SSE `data:` payload shape must be documented
in the endpoint docstring.

**Guardrail 3 — `BackupJob.phase` stays a free-form string, not an enum**

Already true in the current model. **Do not** "tighten" it to a
literal union of toolkit phase names (`Literal["start", "phase_1a_entities", ...]`).
Phase strings are a runtime convention shared between the emitter
and the consumer, not a schema contract. A rewrite that uses
different phase names must still be able to save progress to the
same MongoDB collection.

Check during review: no `Literal[...]` on `BackupJob.phase` or
`BackupJobSnapshot.phase`. No enum for phase.

### Rewrite cost if we decide to switch in v1.1

- **Deletion PR:** ~1 hour. `rm` the two files listed above, delete
  the closure in `api/backup.py`, drop the toolkit dep line.
- **Reimplementation under the unchanged API:** 1–2 weeks for
  correctness-equivalent async rewrite plus tests. But API surface,
  client libraries, MCP tools, and integration test **do not move**.

Risk of v1 entanglement: **low**, conditional on the guardrails above
being enforced in STEP 5.

## Follow-ups regardless of decision

- Add an integration test in STEP 6 that runs a real backup → restore
  round-trip against a seeded namespace. This is the only reliable
  way to catch ordering / ID remapping regressions in either
  implementation.
- Document the chosen approach in `docs/roadmap.md` so the next YAC
  picking up CASE-23 understands which path was taken and why.
