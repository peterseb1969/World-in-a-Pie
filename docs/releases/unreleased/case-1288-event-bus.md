---
section: fixes
cases: [1288]
packages: [wip-auth]
---

### The event bus retries a timed-out publish, and keeps converging a stream with a short max age

- A publish that timed out while the connection was up counted as a
  rejection, so a slow JetStream dropped events after `max_rejections`
  tries. A timeout now backs off and retries; only a refusal counts. The
  queue's max age still bounds how long an event waits.
- A stream whose duplicate window was longer than its declared max age
  (a max age shortened after the window was set) could never be converged:
  JetStream refuses that combination, so every update failed. The window is
  now brought down to the max age.
- Correction to the earlier note: every retry lands inside the duplicate
  window only while the stream's max age is at least the queue's; with a
  shorter max age, a retry older than the window of an event that was in
  fact stored is stored again.
- `stop()` now also waits for a task a reconnect starts during the
  shutdown, and the bus no longer reports itself connected after `stop()`
  returned.
