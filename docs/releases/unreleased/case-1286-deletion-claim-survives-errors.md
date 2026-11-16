---
section: fixes
cases: [1286]
---

### A namespace deletion keeps its claim through a transient MongoDB error, and a losing DELETE leaves the namespace locked

A namespace deletion now keeps its claim through a transient MongoDB error: a heartbeat write that fails is retried at the next tick. At the end of every run, finished or stopped, the claim is released and the cache flush is announced, each on its own, so one that fails no longer skips the other. Previously one failed heartbeat write skipped both: the deletion read as "running" until the Registry restarted, and other services kept caching the deleted namespace. A release write that fails is logged and the claim ages out with the 15-minute lease, and until then `resume-delete` answers 409 "running already"; with NATS unreachable the flush is not announced, and other services' caches expire on their TTL. A `DELETE` that loses to an open deletion of the same namespace no longer unlocks it: while that deletion runs the answer is 409 "running already", and once it has stopped on a failed step the answer is 409 naming `resume-delete`, with the namespace staying locked for it. One exception remains: a stopped deletion recorded before deletions stored the namespace's id (one left by an earlier release) cannot be told apart from a namespace created again under the name, so for it the `DELETE` gives the namespace back with its prior status and answers 409 naming `resume-delete`. `GET …/deletion-status` and `resume-delete` are unchanged.
