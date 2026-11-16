---
section: fixes
cases: [1120]
---

### A namespace deletion stores its journal before it locks the namespace

A deletion locked the namespace first and stored its journal afterwards,
once every storage key had been batched (minutes on a large namespace). A
registry process that died in between (killed, out of memory, or replaced by
a redeploy) left the namespace locked with no journal. `deletion-status`
answered 404, a new `DELETE` 409, and the recovery loop, which reads
journals, never saw it: only a manual `resume-delete` freed it.

The journal is now stored first, held by the deletion's run, then the
namespace is locked, then the journal's steps are stored. A run that dies
anywhere in that window leaves an open journal. Once its claim ages out
(15 minutes), the recovery loop takes it over:

- if the namespace is locked, it builds the steps and finishes the deletion
  as it was requested (the caller and `force` are recorded in the journal);
- if it is not, the deletion never started: the journal is discarded and
  nothing is deleted.

`resume-delete` does the same, and answers 404 for a journal it discards.
Until the claim ages out, a new `DELETE` of that namespace answers 409
("running already"), and `deletion-status` shows the journal with no steps.

A `DELETE` refused because another deletion of the same prefix is open now
locks nothing: the namespace is left as it was. Before, the refused request
had already locked it, and left it locked when the open deletion was of the
same namespace. When storing the steps fails and the namespace cannot be
unlocked either, the 503 now says the namespace stays locked and its deletion
will be finished, instead of saying it is usable again.

A namespace a deletion locked before this change, with no journal, is still
freed by `resume-delete`.
