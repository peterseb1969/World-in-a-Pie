# CI on the development Mac — per-job service containers

**Status:** plan, not implemented. Branch `ci/local-runner`.
**Goal:** run the Gitea Actions CI (triggered by pushes to `gitea`) on the
development Mac instead of `act_runner` on `wip-pi.local`, without CI and local
development interfering with each other.

## What was checked (2026-10-24)

- **Workflows.** `.gitea/workflows/test.yaml` (lint, vendored tarballs,
  security audit and 11 test jobs), `build.yaml` (release images on `v*`
  tags) and `quality-audit.yaml` (on `v*` tags and manually). Every job is
  `runs-on: ubuntu-latest`. The only actions used are `actions/checkout@v4`
  and, in `build.yaml`, `actions/upload-artifact@v4`.
- **Today's test databases** are started by the jobs themselves through
  `scripts/ci-test-container.sh`, as long-lived containers on fixed loopback
  ports, and reached on `localhost`:

  | Job | Database | Port | Variable the tests read |
  |---|---|---|---|
  | test-services (registry, def-store, template-store, document-store) | `test-mongo` | 27017 | `MONGO_URI` |
  | test-wip-auth | `test-mongo` | 27017 | `MONGO_URI` |
  | test-wip-toolkit | `test-mongo` | 27017 | `MONGO_URI` (integration conftest uses `setdefault`) |
  | test-reporting-sync | `test-postgres`, `test-nats` | 5433, 4223 | `POSTGRES_TEST_URI`, `NATS_TEST_URL` |
  | test-ingest-gateway | `test-nats` | 4223 | `NATS_TEST_URL` |

  No test hard-codes these addresses; the toolkit mentions `localhost:27017`
  only in an error message.
- **The conflict.** `scripts/wip-test.sh` provisions containers with **the same
  names and ports** (`test-mongo:27017`, `test-postgres:5433`,
  `test-nats:4223`). It serializes local runs with a machine-wide lock
  (`/tmp/wip-test-infra.lock`); CI takes no lock. On one machine, a CI run
  and a local test run would share and wipe the same databases. CI's own
  parallel jobs also share them today (hence the concurrency tolerance in
  `ci-test-container.sh`).
- **One Podman machine at a time.** `man podman-machine-start`: "Only one
  Podman managed VM can be active at a time." A separate CI machine would mean
  stopping the dev stack whenever CI runs, so it is ruled out. The rootful and
  rootless connections of the one machine have separate container stores but
  share the VM's network, so fixed loopback ports would still collide (not
  tested; it follows from both publishing on the same VM network).
- **This Mac:** arm64 (same as the Pi), macOS 27.0.1, Podman 6.1.4, no `docker`
  CLI, no `/var/run/docker.sock`. The machine socket is
  `$TMPDIR/podman/podman-machine-default-api.sock`. Gitea's web API is
  reachable (`http://gitea.internal:3000/api/v1/version` → 200), which is all
  a runner needs: it polls outward and needs no inbound access.

## Design

**Each test job gets its own databases as workflow `services:`**, on a
per-job network, reached by service name. Nothing is published on host ports.

- **Fixes the conflict for good:** CI can no longer collide with
  `wip-test.sh`, the WIP install or another CI job, and no lock is needed.
- **Retires `ci-test-container.sh`'s concurrency tolerance:** each job owns
  its databases and they are discarded with it.
- **Stays portable:** it is the standard Actions pattern, so the workflow works
  on any runner in container mode (the Pi included) as a side benefit.

**act_runner runs in container mode** on the Mac: each job runs in a Linux
image inside the Podman VM, so jobs see the same environment as on the Pi.
Host mode is rejected: jobs would run on macOS (not what the Pi runs), and it
would need a `docker` CLI and Node installed on the host.

## Workflow changes (test.yaml)

For each job in the table above:

1. Add `services:` with the databases it needs. For example, for
   reporting-sync:

   ```yaml
   services:
     postgres:
       image: postgres:16
       env: {POSTGRES_USER: test, POSTGRES_PASSWORD: test, POSTGRES_DB: wip_test}
     nats:
       image: nats:2
       # needs "-js -m 8222" — see Open question 1
   ```
2. Point the variables at the service names: `MONGO_URI: mongodb://mongo:27017/`,
   `POSTGRES_TEST_URI: postgresql://test:test@postgres:5432/wip_test`,
   `NATS_TEST_URL: nats://nats:4222`.
3. Remove the "Start MongoDB / PostgreSQL / NATS" steps. Replace the
   `docker exec … ping` readiness loops with service `options:` health checks
   (`--health-cmd`), so the job waits for healthy services before its steps run.
4. Leave the other jobs (lint, tarballs, security audit, wip-archive, deployer,
   scaffold, mcp-server, auth-gateway) unchanged: they use no database.

After the cutover: delete `scripts/ci-test-container.sh` once no workflow uses
it. `wip-test.sh` is unaffected; local runs keep their own containers and lock.

## Runner setup on the Mac

1. **Install `act_runner`** (Gitea's runner; a darwin-arm64 build exists).
   Register it against `http://gitea.internal:3000` with a registration token
   from the repository's (or the user's) Actions → Runners page.
2. **Configure it** (a generated `config.yaml`; key names to check against the
   installed version):
   - **labels:** `ubuntu-latest:docker://<job image>` (Open question 2);
   - **socket:** `container.docker_host` pointing at the Podman machine socket,
     with `DOCKER_HOST=unix://…/podman-machine-default-api.sock` in the
     runner's environment;
   - **concurrency:** `runner.capacity` (start at 4, the same as test-services'
     `max-parallel`).
3. **Run it as a launchd user agent**, so it starts at login and restarts if it
   dies.
4. **Release builds** (`build.yaml`) call `podman` inside the job. Either keep
   them on the Pi, or give the release job an image with `podman` and the
   socket mounted. Decide at cutover (Open question 3).

## Cutover

1. **Register the Mac** under a temporary label (e.g. `mac-ci`). Point a copy
   of the workflow on this branch at it, push the branch to `gitea` only, and
   check the run is green and as fast as expected.
2. **Switch the label.** Put `ubuntu-latest` on the Mac runner and remove it
   from the Pi's, or stop the Pi's runner. Two runners with the same label get
   jobs at random.
3. **Merge to `develop`.** Watch the first runs on `develop`.

**Rollback:** re-enable the Pi runner's label. The `services:` workflow also
runs on the Pi in container mode, so no revert is needed.

## Open questions

1. **NATS arguments.** The NATS service needs `-js -m 8222`. GitHub's
   `services:` syntax has no command override. Does Gitea's runner support one
   (a `cmd:` key)? If not: a custom entrypoint via `options:`, or a tiny image
   with a config file that enables JetStream.
2. **Job image.** It needs `git`, Node (for `actions/checkout`), Python ≥ 3.11
   with `venv`, and `curl`. Candidates: an act-compatible Ubuntu image (has
   Node and Python), or `node:20-bookworm` plus `python3-venv`. Pick one, pin
   it, and check its Python version.
3. **Release builds:** keep them on the Pi, or move them too?

## Decided

- **Availability (2026-10-24, Peter):** CI running only while the Mac is
  awake and the Podman machine is up is acceptable. The Pi does not need to
  stay as a fallback runner. A push made while the Mac is asleep is queued by
  Gitea and runs when the runner reconnects. Keep the Pi runner registered but
  without the `ubuntu-latest` label only as the rollback path (see Cutover).

## Verification

- A deliberately failing test on the branch turns the run red; fixing it turns
  it green.
- While a CI run is in progress, `./scripts/wip-test.sh reporting-sync` runs
  locally at the same time, and neither is disturbed.
- Two pushes in quick succession run in parallel without interfering.

## Effort

- The workflow change: about 2 hours (5 jobs plus health checks).
- The runner (install, configuration, launchd, job image): about half a day.
- Cutover and checks: about 1 hour.
