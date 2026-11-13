---
section: fixes
cases: [1286]
---

### A namespace deletion runs once at a time, and a failed-write repair never leaves two active versions

- **A deletion journal is claimed before it runs.** One conditional update
  claims it (no run holds it, and its status is one the run may start from)
  and the run works from the journal that update returns. Before, the only
  guard was an in-process set: another Registry process, or the recovery
  loop holding a copy read before a request finished the journal, ran it
  again from the stale copy, re-ran finished steps and wrote zero counts
  over the real ones. A `resume-delete` that loses the claim answers 409 and
  changes nothing. A claim whose run stopped writing for 15 minutes (a
  process that died) is taken over by the next run.
- **Every journal write sets only the fields it changes** (a step's status,
  error, count and time; the journal's status, summary and completion),
  and only while the run still holds the claim. A run that was taken over
  stops without writing anything more.
- **The repair after a failed successor write steps aside.** When a write
  fails after retiring a document's active version, the repair reactivates
  the highest stored version. It now does so only if that row is not active
  already, and if another write gave the document an active version
  meanwhile, it undoes its own activation, so the document keeps one active
  row.
