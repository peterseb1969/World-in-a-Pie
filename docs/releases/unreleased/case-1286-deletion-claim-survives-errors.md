---
section: fixes
cases: [1286]
---

### A namespace deletion keeps its claim through a transient MongoDB error, and a losing DELETE leaves the namespace locked

A namespace deletion now keeps its claim through a transient MongoDB error: a heartbeat write that fails is retried, and a run that finishes always gives its claim up and tells the other services the namespace is gone — previously a single error mid-deletion left the deletion reading as "running" until the Registry restarted, with other services still caching the deleted namespace. A `DELETE` that loses to another run's deletion of the same namespace no longer unlocks it: the answer is 409 naming `resume-delete`, and the namespace stays locked for the deletion that owns it, instead of coming back as `active` with its data partly deleted. `GET …/deletion-status` and `resume-delete` are unchanged.
