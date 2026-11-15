---
section: fixes
cases: [1265]
packages: [wip-auth]
---

### def-store's startup migrations retry while MongoDB does not answer

def-store retried only the MongoDB connection at startup. Its migrations and
index reconcile ran once, under the 2 s server-selection bound every client
has, so a MongoDB that dropped out for a moment after the connection made
the service exit and wait for the orchestrator's restart. Those steps now
retry the same way (wip-auth's `retry_mongo_step`, for up to 120 s); each is
safe to run again.
