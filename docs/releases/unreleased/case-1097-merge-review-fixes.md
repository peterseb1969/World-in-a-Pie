---
section: fixes
cases: [1097]
---

### A merge carries a deletion, checks only its own templates' tables, and under `older_versions: retire` retires what landed on any failure

- **A document deleted on one side is not an identical copy.** A merge's
  identical-copy check compared data, references and `metadata.custom` but not
  the status, so under `overwrite` / `newer` a document deleted here and active
  in the archive (or the other way round) was left alone as "identical". The
  status is now compared, so the policy decides as for any other difference.
  This corrects the earlier bullet "An identical archive copy adds no
  version", which lists data, resolved references and `metadata.custom` only.
- **The reporting check before documents move looks only at what the merge
  may write.** In a merge it halted on a missing or misshaped reporting table of
  any template in the namespace. It now halts only for the templates whose
  documents the merge may write: the inserted ones, and under `overwrite` /
  `newer` every clashing one, since whether a clash is identical or older is
  decided only as documents are written (and on unusable bookkeeping). Another
  template's broken table is reported as a warning and the merge goes on.
- **A batch that fails partway retires what landed under `older_versions:
  retire`, whatever the failure.** Only the engine's own errors were caught; a
  driver failure (a lost connection, a timeout) left the landed rows' older
  versions active as well. (Under `keep`, older versions stay active by
  design.) If that recovery fails too (the connection is still down), the
  merge's error is still the insert's, and it says which step could not be
  done and that documents in the batch may hold two active versions.
- **A merge whose writes all landed is not failed by its file recount.** When
  the recount of file references after a complete merge failed (a transient
  MongoDB error), the job failed, skipping the merge's claims, synonym release
  and provenance, and an operator re-ran a merge that had already applied. It
  is now a warning that the counts may be stale until the next write to each
  file's documents, as it already was after a failed merge.
