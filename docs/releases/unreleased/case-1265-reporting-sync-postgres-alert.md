---
section: fixes
cases: [1265]
---

### One slow PostgreSQL probe no longer pages as a lost connection

reporting-sync's alert check raised a CRITICAL "Lost connection to
PostgreSQL" on a single failed probe, and the probe's 1 s budget includes
waiting for a pool connection, so a busy pool paged. The alert now needs two
failed probes in a row, a check interval apart (30 s by default). A NATS
loss, which the client reports rather than probes, still alerts at once.
`/health` still reports each probe as it is.
