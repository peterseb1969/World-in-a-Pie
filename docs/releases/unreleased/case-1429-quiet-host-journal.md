---
section: operational
cases: [1429]
packages: [wip-auth]
---

### A quiet install no longer floods the host's journal

A podman machine running one dev stack and the test suites wrote 87,000 to
107,000 journal lines per 10 minutes. The 4 GB journal therefore rotated
within hours, and incidents were gone before anyone looked. Three sources are
fixed or made configurable:

- **Health probes are not logged when they succeed.** `wip_auth.configure_logging`,
  which every backend service calls, and the MCP server's HTTP transport drop
  successful `GET`/`HEAD` access lines for `/ready` and `/health` (bare or
  under `/api/<service>/`). A failing probe, a deeper path such as
  `/health/integrity`, and every other request are still logged.
- **Test containers log to a size-capped file, not the journal.**
  `scripts/wip-test.sh` starts its MongoDB, PostgreSQL and NATS with
  `--log-driver k8s-file` (`json-file` under docker) and `max-size=10m`.
  `podman logs` still works after a failed run. A container left from before,
  still logging to the journal, is created again on the next run.
- **Host settings come from a new script, `scripts/podman-machine-tune.sh`.**
  It sets the podman service to `--log-level=warn`; at the default `info`,
  every healthcheck wrote four lines. With `--journal-max SIZE` it also caps
  the journal. `--measure` reports the journal's lines per 10 minutes by
  source. It runs inside the podman machine on macOS, or on the Linux host.

**Action:** run `scripts/podman-machine-tune.sh` once per podman host, then
restart the containers it lists as still logging at `info`. Size
`--journal-max` from `--measure` once the sources above are quiet
(`docs/deploy/podman/README.md`).
