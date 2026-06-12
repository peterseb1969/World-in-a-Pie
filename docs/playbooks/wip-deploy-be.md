# wip-deploy usage — BE-YAC / operator playbook

**Scope:** how to *drive* wip-deploy — install lifecycle, the verbs, dev-target
mechanics, recovery. The app-side *contract* (what an app must satisfy to be
deployable) lives in `FR-YAC/papers/wip-deployable-app-contract.md`; the
APP-YAC self-deploy loop lives in `docs/playbooks/app-builder/wip-deploy-app.md`.
The `/wip-deploy` slash command (CASE-298) is the routinized
pre-flight→operate→smoke recipe for deploys against shared targets; this
playbook is the reference underneath it.

*Provenance: distilled from the 2026-06-11/12 hot-wire redeploy sessions
(CASE-451/455/457/458). Every command shown was run or read from the CLI's
own help in those sessions.*

---

## 0. Getting the CLI

`wip-deploy` is the `deployer/` package's entry point and is **not installed
by default** — a fresh clone's venv doesn't have it:

```bash
cd <repo>/deployer && ../.venv/bin/pip install -e .
.venv/bin/wip-deploy --help
```

`wip-deploy examples` prints the canonical invocations for common workflows
and is worth reading once in full.

## 1. Install anatomy

One **install** = preset × target × name:

- **preset** (`-p`): which optional modules ride along. `full` =
  reporting-sync + ingest-gateway + minio + mcp-server on top of core.
  Others: `core`, `standard`, `analytics`, `headless` (see `--preset` help).
- **target** (`-t`): `compose` (production-style containers), `dev`
  (hot-reload: bind-mounted source, local app builds), `k8s` (manifests).
- **name** (`--name`): determines the install dir `~/.wip-deploy/<name>/`
  and the volume/secret identity. **Reusing a name reuses its data volumes
  and secrets** — that's how you convert an install between targets without
  losing data.

`~/.wip-deploy/<name>/` contains the rendered `docker-compose.yaml` (or
`services/` for k8s), `config/` (Caddyfiles etc.), `secrets/` (api-key,
dex passwords, …), and **`deployment.deployer-state`** — the persisted spec.

**State semantics (CASE-455):** the state file records what was *applied*.
Post-mutation failures (e.g. health-wait timeout after containers were
recreated) still persist the new spec; only pre-mutation failures (render
error, build failure, missing binary) keep the previous state as
last-known-good. If `status`/`add-app` ever claim a different target than
what's running, you're on a pre-CASE-455 state file — re-run the exact
install command once green to heal it.

## 2. The verbs

| Verb | Use when | Notes |
|---|---|---|
| `install` | First install, or any spec change (target, preset, apps, modules) | Re-runs are safe: secrets/volumes reused by name. CLI `--tag` beats manifest pins (CASE-438). |
| `up --name <n>` | Bring an existing install back to running, **no spec change** | The verb after a podman-machine restart. |
| `nuke --name <n>` | Tear down | **Default preserves data volumes AND secrets** — reinstall reuses both. `--remove-data` kills databases; `--purge-all` removes every `wip-*` container host-wide; `--remove-secrets` clears secrets of *every* install. `--dry-run` first when in doubt. |
| `rebuild <svc> …` | Dockerfile / requirements.txt changed | Compose `up -d --build --force-recreate` for just those services. `--no-wait` to skip health polling. |
| `restart <svc>` | — | For backend source-only edits in dev mode, `podman restart wip-<svc>` does the same (CLAUDE.md §7). |
| `add-app NAME --app-source <path>` | Add one app to a running install without touching the rest | Dev-target only for `--app-source`. Reads the persisted state — see CASE-455 note above. |
| `remove-app`, `add-module`, `remove-module` | Single-element spec mutations | Same persisted-state mechanics as add-app. |
| `status --name <n>` | What's deployed, on which images | `--diff` re-renders from persisted spec and compares. |
| `render` / `show-spec` / `validate` | Inspect without applying | `show-spec --preset full --target dev` answers "what would this preset give me". |
| `check-app-deployability <path>` | **Before any `--app-source`** | See the APP playbook for the 7 checks and the subdirectory rule. |

## 3. Dev target specifics

`--target dev` is the hot-wired mode:

- **Backend services**: each service's `src/` and `libs/wip-auth/src` are
  bind-mounted read-only; uvicorn runs `--reload`. Source edits land on
  save or at worst `podman restart wip-<svc>` (~3 s). Model/route changes
  sometimes need the restart even with `--reload`.
- **Apps**: built locally from `--app-source NAME=PATH` checkouts
  (Dockerfile.dev), source bind-mounted rw, deps in a named volume. Details
  in the APP playbook. **The path must be the app directory, not
  necessarily the repo root** — some repos keep the app in a subdirectory
  (`WIP-ClinTrial/clintrial-explorer`, `WIP-DnD/apps/dnd-compendium`); the
  failure for a wrong path is a bare "Dockerfile not found in <path>".
  Run `check-app-deployability` first, always.
- **Image-source policy (CASE-282/355):** a dev-target app must come from
  `--app-source` (local build) or be explicitly opted into registry images
  via `--app-from-registry NAME`. A bare enabled app fails loudly at render.
- Dev installs always rebuild + force-recreate on install, and wipe stale
  `wip-*` containers from prior projects first (CASE-282).

Full 7-app hot-wired reference invocation (2026-06-12, wip-local):

```bash
wip-deploy install --name wip-local --target dev --preset full \
  --app-source react-console=$HOME/Development/WIP-ReactConsole \
  --app-source wip-kb=$HOME/Development/WIP-KB \
  --app-source wip-val=$HOME/Development/WIP-VAL \
  --app-source wip-aa=$HOME/Development/WIP-AA \
  --app-source clintrial=$HOME/Development/WIP-ClinTrial/clintrial-explorer \
  --app-source dnd=$HOME/Development/WIP-DnD/apps/dnd-compendium \
  --app-source song=$HOME/Development/WIP-Song
```

## 4. Recovery scenarios

**Podman machine died (Mac slept overnight):** symptoms — every `podman`
command fails with "Cannot connect to Podman socket"; the whole stack
unreachable. Fix: `podman machine start`, then `wip-deploy up --name <n>`.
Data volumes survive; containers come back with their healthchecks.

**Install timed out on one unhealthy app:** the apply *happened* — fix the
app (its logs: `podman logs wip-<app>`), then either wait for the
healthcheck to flip or re-run the same install for a clean green apply.
Since CASE-455 the state stays truthful either way.

**A service is healthy but unreachable / silent empty 200s:** deployer
territory, not service code — check the rendered Caddyfile and routes
(CLAUDE.md §6 "When a bug isn't in service code", §14 for the
Caddy-200-on-unmatched-path trap).

**`nc -z localhost <port>` before waiting on anything** that depends on a
host-bound port (CASE-319/320 verify-before-wait).

## 5. Discipline

Deploys against shared targets (wip-kb, wip-stable) go through the
`/wip-deploy` slash command — mandatory pre-flight, operation, mandatory
smoke. The smoke's bellwether is `GET /api/registry/health` through the
external hostname with **content validation** — never trust a bare 200
(§14). After any deploy, run the actual code path your change touches, not
just health endpoints (CLAUDE.md §4.2).
