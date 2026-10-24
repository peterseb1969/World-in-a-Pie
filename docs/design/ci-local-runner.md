# CI on the development Mac — per-job service containers

**Status:** implemented 2026-10-24. The Mac runner (`mac-ci`, gitea-runner
v5.2.2, `~/gitea-runner/`, launchd agent `local.gitea-runner`) holds
`ubuntu-latest`. The Pi runner stays registered with only `pi-host:host` (its
config backup: `~/act_runner/config.yaml.bak-20261024`). First real run on
the Mac (all 16 jobs, green): about 1 min 45 s; the Pi took about 10 min.
**Goal:** run the Gitea Actions CI (triggered by pushes to `gitea`) on the
development Mac instead of `act_runner` on `wip-pi.internal`, without CI and local
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

  The test defaults can all be overridden from the environment. One test
  helper did ignore it: the toolkit integration conftest's MongoDB
  reachability probe hard-coded `("localhost", 27017)`, so with MongoDB as a
  job service all 10 integration tests skipped silently. An earlier version
  of this plan said no test hard-codes these addresses; that grep only
  matched the `host:port` string form. Fixed: the probe now reads
  `MONGO_URI`.
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

**gitea-runner runs in container mode** on the Mac: each job runs in a Linux
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
       cmd: ["-js", "-m", "8222"]   # Gitea-specific key, see Decided
   ```
2. Point the variables at the service names: `MONGO_URI: mongodb://mongo:27017/`,
   `POSTGRES_TEST_URI: postgresql://test:test@postgres:5432/wip_test`,
   `NATS_TEST_URL: nats://nats:4222`.
3. Remove the "Start MongoDB / PostgreSQL / NATS" steps. Replace the
   `docker exec … ping` readiness loops with service `options:` health checks
   (`--health-cmd`), so the job waits for healthy services before its steps run.
4. Leave the other jobs (lint, tarballs, security audit, wip-archive, deployer,
   scaffold, mcp-server, auth-gateway) unchanged: they use no database.

`scripts/ci-test-container.sh` was deleted after the cutover (no workflow used
it any more). `wip-test.sh` is unaffected; local runs keep their own
containers and lock.

## Runner setup on the Mac

1. **Install `gitea-runner`** (Gitea's runner, formerly `act_runner`; the
   project moved to `gitea.com/gitea/runner`, and v5.2.2 ships a
   darwin-arm64 binary).
   Register it against `http://gitea.internal:3000` with a registration token
   from the repository's (or the user's) Actions → Runners page.
2. **Configure it** (a generated `config.yaml`; key names to check against the
   installed version):
   - **labels:** `ubuntu-latest:docker://localhost/wip-ci:py3.13-trixie`, with
     `container.force_pull: false` so the locally built image is used;
   - **socket:** `container.docker_host` pointing at the Podman machine socket,
     with `DOCKER_HOST=unix://…/podman-machine-default-api.sock` in the
     runner's environment;
   - **DNS:** `container.options: --dns-search .`. Required: this Mac's DNS
     configuration adds the search domain `local`, so every lookup of a name
     that does not exist also tries `<name>.local`, which macOS resolves by
     multicast DNS and abandons after ~3 s. The service tests point at names
     that do not exist in CI (`http://registry`, the other services), so the
     wait repeats in every test: template-store ran at about one test per
     20 s. Measured in a job container (2026-10-24): `registry` 3.0 s and
     `def-store` 10.0 s with the search domain, 4–6 ms without, and service
     names (`mongo`) still resolve;
   - **pip cache:** add `-v wip-ci-pip-cache:/root/.cache/pip` to
     `container.options` (and list the volume in `container.valid_volumes`),
     so job containers share one pip cache instead of downloading from
     scratch;
   - **concurrency:** `runner.capacity: 16`, one slot per job in
     `test.yaml`. No job `needs:` another, so a push runs all of them at
     once.

   **Measured (2026-10-24, `gitea-runner exec` of the whole `test.yaml`
   with these options, while the WIP dev stack was stopped):** all 16 jobs
   passed (5,199 tests) in 82 s with a partly warm pip cache, and in 79 s
   with a fully warm one (0 downloads). The longest job is def-store
   (55 s of tests). The 8 database jobs at once used about 1 GB of the VM's
   8 GB.
3. **Run it as a launchd user agent**, so it starts at login and restarts if it
   dies.
4. **Release builds** (`build.yaml`) call `podman`, which a job container
   cannot reach. They run in **host mode** on the same runner, under a second
   label `macos-host:host`, using the Mac's own podman (native arm64 builds,
   as on the Pi) and its layer cache. The launchd agent sets `PATH` to
   include `/opt/homebrew/bin` (Node for the actions, podman). The job's
   `podman login` stores the registry login in the Mac user's podman auth
   file.

## Cutover

1. **Register the Mac** under a temporary label (e.g. `mac-ci`). Point a copy
   of the workflow on this branch at it, push the branch to `gitea` only, and
   check the run is green and as fast as expected.
2. **Switch the label.** Put `ubuntu-latest` on the Mac runner and remove it
   from the Pi's, or stop the Pi's runner. Two runners with the same label get
   jobs at random.
3. **Merge to `develop`.** Watch the first runs on `develop`.

**Rollback:** not a label flip. The Pi's runner (`act_runner` v1.0.8,
`~/act_runner/config.yaml` on `wip-pi.internal`) runs in **host mode**
(`labels: ["ubuntu-latest:host"]`): jobs run directly on the Pi's Debian, and
host mode does not run service containers. To roll back, either revert
`test.yaml` to the pre-cutover commit, or switch the Pi to container mode
(`ubuntu-latest:docker://<job image>`), which also needs a runner that honours
the services `cmd:` key (Open question 4).

## Open questions

1. *(Resolved; see Decided.)*
2. **Job image.** *(Decided 2026-10-24, Peter: the last row below, at
   `ci/runner-image/Dockerfile`.)* Today's jobs
   run on the Pi host, so they use the Pi's Python 3.13.5, Node 20.19.2,
   npm 9.2.0 and git 2.47.3; the local `.venv` is Python 3.13 too. The image
   needs `git`, Node (for `actions/checkout`), npm (the tarball check),
   Python 3.13 with a working `venv`, `curl`, `bash` and `tar`. Probed on
   2026-10-24:

   | Image | Python | venv | Node / npm |
   |---|---|---|---|
   | `node:22-trixie` | 3.13.5 | missing | 22 / 10.9 |
   | `python:3.13-trixie` | 3.13.17 | ok | missing |
   | `docker.gitea.com/runner-images:ubuntu-latest` | 3.12.3 | ok | 24 / 11.19 |
   | `python:3.13-trixie` + Debian `nodejs npm` | 3.13.17 | ok | 20.19.2 / 9.2.0 |

   **Recommended: the last row**, a two-line image built on the Mac:

   ```dockerfile
   FROM docker.io/library/python:3.13-trixie
   RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*
   ```

   - It is Debian trixie, like the Pi, so Node, npm and git are the exact
     versions CI uses today, and Python differs only in the patch release.
   - It is built locally (`podman build -t localhost/wip-ci:py3.13-trixie`)
     and the runner label points at it with `force_pull: false`, so no
     registry is needed. Pin the base by digest when it is committed.
   - Size: 1.3 GB, pulled once.
3. *(Resolved; see Runner setup step 4.)*
4. **Pi runner and rollback.** The Pi runs `act_runner` v1.0.8 in host mode.
   Only relevant if the rollback path should switch the Pi to container mode:
   does v1.0.8 honour services `cmd:`? Not tested.

## Decided

- **Release builds move to the Mac, host mode (2026-10-24, Peter).** Tested
  before the switch: `build-release.sh` built all 8 images natively on the
  Mac in 257 s, and a host-mode job in the launchd agent's environment found
  Node 26 and podman 6.1.4. The workflow's "Generate production compose" and
  artifact-upload steps were removed with `build-release.sh
  --generate-compose`: the option edited `docker-compose.production.yml`,
  which was deleted in the v1 retirement (2026-04-30) because wip-deploy now
  renders production compose, so the step had been failing since then.
- **Quality audit** (`quality-audit.yaml`, `v*` tags) runs in a job
  container like the tests and works there. It currently fails against its
  baseline (pinned 2026-07-27, at v2.1.0): mypy 66 vs 0, radon-cc-c-plus 215
  vs 142, vulture 2 vs 1. That is drift since the baseline, not the runner
  move.

- **NATS arguments: the services `cmd:` key (2026-10-24, tested).** Gitea's
  runner adds a Gitea-specific `cmd` list to service containers (GitHub's
  syntax has none). It replaces the image's default command. Tested with
  `gitea-runner` v5.2.2 `exec` against the Podman machine socket, using two
  jobs on `nats:2` and checking `http://nats:8222/jsz` from the job
  container:
  - with `cmd: ["-js", "-m", "8222"]`: JetStream enabled (`config.store_dir`
    set, `/healthz?js-enabled-only=true` returns `ok`);
  - without `cmd:`: `"disabled": true`. The image's default command
    enables monitoring but not JetStream.

  The same run showed the rest of the design working on this Mac:
  - job containers on the Podman socket;
  - a per-job network;
  - service containers reached by name.

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
