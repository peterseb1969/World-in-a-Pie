---
section: fixes
cases: [1097]
---

### A merge carries a deletion, checks only its own templates' tables, and retires what landed on any failure

- **A document deleted on one side is not an identical copy.** A merge's
  identical-copy check compared data, references and `metadata.custom` but not
  the status, so under `overwrite` / `newer` a document deleted here and active
  in the archive (or the other way round) was left alone as "identical". The
  status is now compared, so the policy decides as for any other difference.
- **The reporting check before documents move looks only at what the merge
  writes.** In a merge it halted on a missing or misshaped reporting table of
  any template in the namespace. It now halts only for the templates whose
  documents the merge writes (and on unusable bookkeeping). Another template's
  broken table is reported as a warning and the merge goes on.
- **A batch that fails partway retires what landed whatever the failure.** Only
  the engine's own errors were caught; a driver failure (a lost connection, a
  timeout) left the landed rows' older versions active as well.
