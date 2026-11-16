---
section: fixes
cases: [1275]
---

### The startup replay-stream sweep waits for NATS

document-store deletes, at startup, the replay streams an earlier run left
behind. On a whole-stack start (a host reboot, `wip-deploy stop` and `start`)
NATS connects after document-store has started, so the sweep found no
connection and did nothing, and those streams stayed for their whole 24-hour
age-out window. The sweep now waits until NATS is connected, then runs, and
prints `Replay stream sweep: deleted N orphaned stream(s)`. A sweep that
cannot list the streams says so instead of printing a count.
