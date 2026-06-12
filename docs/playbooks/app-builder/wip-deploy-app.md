# wip-deploy for APP-YACs — getting your app into a dev install and living there

**Scope:** the self-deploy loop — is my repo deployable, how does it get
included, what runtime the dev install gives me, how the hot-reload loop
works, how to debug my container. The *contract* your app must satisfy
(Dockerfile.dev shape, vite binding, base-path rules, platform invariants)
is owned by `FR-YAC/papers/wip-deployable-app-contract.md` — read that
first when building; read this when deploying. Operator-side lifecycle
(installs, nuke, recovery) is `docs/playbooks/wip-deploy-be.md` in
World-in-a-Pie.

*Provenance: distilled from the 2026-06-11/12 sessions that hot-wired all
seven apps (CASE-451/455/457/458). Every command and env var shown was
observed live, not recalled.*

---

## 1. Step zero, always: check-app-deployability

Before anyone passes your checkout to `--app-source`:

```bash
wip-deploy check-app-deployability /path/to/your/app
```

Seven checks: `Dockerfile.dev` at the **source root you pass**, vite binds
`host: '0.0.0.0'`, vite proxy ports match your manifest, a `dev` script in
package.json, a matching app manifest discoverable (or `--manifest <path>`),
manifest validates (CASE-353), and friends. `✓ 7 check(s) passed` or a
per-check explanation.

**The subdirectory rule:** the path is your *app directory*, which is not
always your repo root. WIP-Song's app lives at the repo root;
WIP-ClinTrial's lives at `clintrial-explorer/`; WIP-DnD's at
`apps/dnd-compendium/`. If the deployer says "Dockerfile not found in
<path>", you (or the operator) passed the wrong level — the error doesn't
hint at this. Tell the operator which directory is the app.

## 2. Getting included

Two halves:

1. **Platform half:** an app manifest at `apps/<name>/wip-app.yaml` in
   World-in-a-Pie (your handoff: name, base path, http port, dev port,
   namespace, healthcheck). Without it, the deployer doesn't know your app
   exists.
2. **Install half:** on a running dev install, one verb:

   ```bash
   wip-deploy add-app <name> --name <install> --app-source /path/to/app-dir
   ```

   or, as part of a full install, one more `--app-source <name>=<path>`
   flag. Either way the image builds from *your checkout's* `Dockerfile.dev`.

**Rolling a production install to your new image (CASE-410):** once your
repo's CI has pushed a sha-tagged image, one verb points the install at it:

```bash
wip-deploy app-deploy <name> --tag sha-<short> --name <install>
```

This works from any directory — including your own repo — on installs made
after CASE-459 (the deployer reads the WIP checkout location from the
install's own state). On an older install you'll get "discovery found no
components/apps under <path>": run it from the World-in-a-Pie root, pass
`--repo-root`, or set `WIP_REPO_ROOT` — the error is about *where you ran
it from*, not your app or the install.

## 3. What the dev render gives you

Observed from a live render (wip-song, 2026-06-12):

| What | Value | Notes |
|---|---|---|
| Image | `<name>:dev`, built from your `Dockerfile.dev` | rebuilt on every install |
| Source | your checkout bind-mounted **rw** at `/app` | edits appear in-container instantly |
| Dependencies | named volume shadowing `/app/node_modules` | **independent of your host node_modules** — see §4 |
| `APP_BASE_PATH`, `VITE_BASE_PATH` | `/apps/<name>` | serve everything under this |
| `PORT` | your manifest's http port | the healthcheck targets it |
| `WIP_BASE_URL` | `http://wip-router:8080` | in-network; never localhost |
| `MCP_URL` | `http://wip-router:8080/mcp` | requires `X-API-Key` on every request (CASE-451) |
| `WIP_NAMESPACE` | your dev namespace | |
| `WIP_API_KEY` | **the install admin key** (`${API_KEY}`) | ⚠ see below |

**The admin-key caveat (CASE-457):** the injected key is multi-namespace,
so the "namespace derivation is automatic, omit the parameter" contract
your app was scaffolded against does NOT hold under it. Value-form reads
with no explicit namespace can silently return **zero rows** instead of
erroring. Until the platform fixes land, scope reads explicitly —
`?namespace=$WIP_NAMESPACE` on proxied WIP calls (WIP-Song ships a
middleware for this; see CASE-457's workaround) — or don't rely on
derivation in dev.

**Routing:** Caddy serves `https://localhost:8443/apps/<name>/` →
forward-auth (gateway) → your container. Unmatched paths under the router
return **empty 200, not 404** (§14 trap) — never use a bare 200 as
evidence your route works; check the body.

## 4. The hot-reload loop and its boundary

- **Source edits**: saved file → rw bind mount → your dev server
  (vite / tsx watch / nodemon per your `dev` script) reloads. No deployer
  involvement. This is the whole point of dev mode — observed end-to-end:
  a server-code fix went from edit to healthy container in seconds
  (CASE-451).
- **Dependency or Dockerfile.dev changes**: the node_modules named volume
  does NOT see your host `npm install`. You need:

  ```bash
  wip-deploy rebuild <name> --name <install>
  ```
- **Manifest changes** (ports, routes, env): a re-render —
  `wip-deploy install` with the same flags (operator) or `add-app` again.

## 5. Debugging your container

```bash
podman ps --format "{{.Names}}  {{.Status}}"   # is it (healthy)?
podman logs --tail 50 wip-<name>               # your dev server's output
podman exec wip-<name> env | grep WIP          # what you actually received
```

The healthcheck hits `http://localhost:<PORT>/apps/<name>/api/health`
*inside* the container — if your server dies at startup, the container
shows `(unhealthy)` while vite may still be running; read the logs for the
`[0]`/`[1]` concurrently streams separately. To prove a platform endpoint
works independently of your code, probe it from inside your own container
with your own env (the CASE-451 pattern):

```bash
podman exec wip-<name> sh -c \
  'wget -qO- --header="X-API-Key: $WIP_API_KEY" $WIP_BASE_URL/api/registry/health'
```

If that succeeds and your app still fails, the bug is on your side of the
env — which is exactly what it turned out to be both times this pattern
was used.
