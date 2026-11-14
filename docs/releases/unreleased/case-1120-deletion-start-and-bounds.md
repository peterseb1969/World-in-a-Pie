---
section: fixes
cases: [1120]
packages: [wip-client]
---

### A namespace deletion that cannot start leaves the namespace as it was; the journal is bounded; resume-delete needs force where a deletion does

- **A deletion that cannot store its journal is undone, not stuck.** The
  namespace is locked, then its stored-object keys are written in batches
  and the journal is stored. A database error there used to answer 500 and
  leave the namespace locked with no journal. Nothing has been deleted at
  that point, so now the batches written are dropped, the namespace gets
  its status back, and the answer is **503**. A `resume-delete` that cannot
  build the journal of a namespace locked with none drops its batches, stays
  locked, and answers 503; it can be tried again.
- **The journal stays one bounded document.** Stored-object keys are kept
  outside the journal in batch documents of 1,000, and they are now streamed
  into those batches instead of being read into memory first. Inline, the
  keys alone would pass MongoDB's 16 MiB document limit at about 350,000
  files (an earlier note said about 240,000; that figure was wrong). The
  broken inbound references are bounded too: the journal keeps the first
  1,000 and counts every one per kind and source namespace
  (`broken_references_counts`, with `broken_references_truncated`, in
  `deletion-status`).
- **Key batches no longer pile up.** A step left undone (the namespace was
  created again) drops its batch. At startup, the Registry indexes the
  batches by namespace and drops every batch older than an hour that no
  unfinished step needs: those of a journal that was never stored, or of a
  step that completed without dropping its own. A failed or degraded
  deletion keeps its batches, because resuming it needs them.
- **A file-storage step that names no keys is degraded**, not a clean step
  that deleted nothing (a journal written by a newer Registry, read after a
  rollback).
- **Counts add up across runs.** A degraded step run again adds what it
  deletes to what it deleted before, and the summary counts every step that
  ran, including one later left undone. It now says what the whole
  deletion removed.
- **`resume-delete` records the caller as the requester**, not
  "resume-delete". Its answers, and `DELETE`'s 503, are declared in the
  OpenAPI spec, and `NamespaceDeletionJournal` carries the reference counts.
  Its new refusals are under action-required (`case-1286-one-open-deletion`).

The earlier unreleased note on large namespaces is corrected to match:
stored-file keys are in the batch documents, not on the steps.
