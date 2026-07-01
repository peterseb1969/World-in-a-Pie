<p align="center">
  <img src="images/WIP_logo_blue_small.png" alt="World In a Pie" width="160">
</p>

# Setting up an APP-YAC — tutorial

An **APP-YAC** is an AI coding agent (a Claude Code session) scoped to building **one application on top of a running WIP**. It's set up so the agent has, in its project directory:

- **`.mcp.json`** → WIP's MCP tools (discover templates, query documents, manage terminologies, import data — without reading WIP's source)
- **app-builder slash commands** (`/explore`, `/design-model`, `/build-app`, …)
- **client libraries** (`@wip/client`, `@wip/react`) + the `wip-toolkit` wheel
- a **`CLAUDE.md`** carrying the APP-YAC role and conventions

> **APP-YAC vs BE-YAC.** This guide is for building apps *on top of* WIP. To work *on WIP itself* (backend services), that's a **BE-YAC** — `./scripts/setup-backend-agent.sh`, a different path. `setup-backend-agent.sh` is **not** part of APP-YAC setup.

> ⚠️ **Draft.** The scaffolding flow is read from `scripts/create-app-project.sh` and `docs/WIP_AppSetup_Guide.md`; the exact `/mcp`-connects-first-try behaviour hasn't been re-run for this doc. Verify-items are flagged at the end.

---

## The one tool that does it: `create-app-project.sh`

Everything is done by **`create-app-project.sh`**, run **from your World-in-a-Pie clone**. It auto-detects what to do from the target directory — you don't pick a mode:

- **empty / new dir → CREATE** a fresh app project (scaffold + `git init`) — *[Scenario B](#scenario-b--a-fresh-app-yac-new-app)*
- **populated dir → SET UP IN PLACE** — add the APP-YAC scaffolding to an existing app repo without disturbing its working tree or `CLAUDE.md` — *[Scenario A](#scenario-a--an-app-yac-for-an-existing-app)*

It generates `.mcp.json` pointing at your **running WIP install** (it auto-detects the install and uses its `secrets/api-key` via `WIP_API_KEY_FILE`, so key rotation just works), copies the app-builder slash commands + reference docs, and drops in the client-lib tarballs.

---

## Prerequisites

1. **A running WIP instance** with the MCP server (the `standard` or `full` preset includes `mcp-server`). Bring one up with a deploy guide:
   - single host → **[podman quickstart](deploy/podman/README.md)** · local dev → **[dev guide](deploy/dev/README.md)** · cluster → **[Kubernetes](deploy/k8s/README.md)**
   - If your app will integrate with existing data, have that **data model bootstrapped** in the instance first.
2. **Your World-in-a-Pie clone** on `develop`, with a `.venv` that has the **MCP server installed**:
   ```bash
   cd /path/to/World-in-a-Pie
   .venv/bin/pip install -e components/mcp-server/
   ```
   The app's `.mcp.json` runs `python -m wip_mcp` **from this clone's venv**, and the `mcp` SDK it needs is **not** pulled by the deploy guides' `pip install -e deployer`. Skip this and the MCP server fails to start — the symptom is **error `-32000`** on connect. The clone must also stay put (its path is baked into `.mcp.json`).
3. **Claude Code**.

Sanity-check the instance is reachable before wiring an app:
```bash
curl -k https://localhost:8443/api/registry/namespaces
```

---

## Scenario A — an APP-YAC for an existing app

You already have an app repo and want to give it an APP-YAC (MCP + slash commands + role), in place.

```bash
cd /path/to/World-in-a-Pie
git switch develop
./scripts/create-app-project.sh /path/to/your-existing-app --name "Your App"
```

Because the directory is populated, this runs **set-up-in-place** (idempotent): it adds/refreshes `.mcp.json`, `.claude/commands/`, reference docs, and the client libs, and writes a fresh role file to **`CLAUDE.md.refresh`** — it does **not** overwrite your app's own `CLAUDE.md`, working tree, or git state.

Then:
1. Review `CLAUDE.md.refresh` and merge anything you want into your `CLAUDE.md` (or run with `--force-claude-md` to overwrite — only if you have no app-specific CLAUDE.md content to keep).
2. Open the app directory in Claude Code — that session is your APP-YAC. Verify MCP (below).

---

## Scenario B — a fresh APP-YAC (new app)

Green-field: scaffold a brand-new app project. Use a **clean directory, not inside another app's repo** (the agent shouldn't see other apps' source).

```bash
cd /path/to/World-in-a-Pie
git switch develop
./scripts/create-app-project.sh ~/Development/my-new-app --name "My New App"
```

Empty dir → **CREATE**: full scaffold (`.mcp.json`, `.claude/commands/`, `docs/`, `libs/`, a starter `CLAUDE.md`) + `git init`.

Then open `~/Development/my-new-app` in Claude Code — that's your APP-YAC. Start with `/explore` to discover the running instance's data model, then `/design-model` and `/build-app`.

> **Tier 3 (cross-agent cases):** add `--kb <url>` to either scenario to enable the KB case workflow; without it the project stays tier 2 (solo). An existing `kb.json` is preserved.

---

## Verify the MCP connection (both scenarios)

1. Open the app directory in Claude Code (MCP servers spawn on startup — if you opened it before running the script, restart).
2. Run `/mcp` — you should see the **`wip`** server with its tools (**94 tools, 5 resources**).
3. If tools are missing:
   - Is a WIP instance **running**? (`.mcp.json` points `WIP_API_KEY_FILE` at a live install's `secrets/api-key`.)
   - **Wrong install in `.mcp.json`:** the generated `WIP_API_KEY_FILE` can fall back to `~/.wip-deploy/wip-local/secrets/api-key` when auto-detect misses your install — but the deploy guides install with `--name wip`, so there's no `wip-local`. Open `.mcp.json` and repoint `WIP_API_KEY_FILE` (and the `*_URL`s) at your actual install: `~/.wip-deploy/<your-name>/secrets/api-key`.
   - **Error `-32000` / the server won't start:** two common causes — (a) the WIP clone's venv is missing `wip_mcp` (`cd <WIP clone> && .venv/bin/pip install -e components/mcp-server/`); (b) `WIP_API_KEY_FILE` points at a nonexistent install (above). Check the venv with `PYTHONPATH=components/mcp-server/src .venv/bin/python -c "import wip_mcp"`, then restart Claude.
   - **Restart Claude Code** — it only spawns MCP servers at startup.
   - Deeper: `docs/WIP_AppSetup_Guide.md` → *"MCP Server Not Connecting."*

---

## The build loop (once the APP-YAC is live)

The app-builder slash commands drive a 4-phase process — `/explore` (discover the data model) → `/design-model` (templates + terminologies) → `/build-app` (the SPA/server) → `/improve`. The full method is in `docs/AI-Assisted-Development.md` (copied into the app's `docs/`).

**Two keys, two jobs:** the MCP server uses a **privileged** key (cross-namespace) — fine for discovery/setup. Your **app's runtime** should use a **namespace-scoped** key (single-namespace keys auto-derive the namespace, so app calls omit it). See the generated `CLAUDE.md`'s API-key section.

---

## See also

- **[WIP App Setup Guide](WIP_AppSetup_Guide.md)** — the detailed reference (pre-flight checklist, manual `.mcp.json`, file lists, CLAUDE.md structure, known issues).
- **Deploy a WIP instance** — [podman](deploy/podman/README.md) · [dev](deploy/dev/README.md) · [k8s](deploy/k8s/README.md).
- **Work on WIP itself** (not an app) — BE-YAC via `./scripts/setup-backend-agent.sh`.

---

## Verify-items (draft — confirm on a real run)

1. **Scenario A CLAUDE.md handling** — confirm the `CLAUDE.md.refresh` behaviour and the `--force-claude-md` flag on a real populated-dir run.
2. **Running-install auto-detection** — confirm the script picks up the intended install's `secrets/api-key` when exactly one WIP install is running (and what to pass — `WIP_API_KEY_FILE_OVERRIDE` — when several are).
3. **`/mcp` shows the tools first try** end-to-end after `create-app-project.sh` + the `pip install -e components/mcp-server/` step, against a live instance.

*(Resolved: the venv must have `wip_mcp` installed — `pip install -e components/mcp-server/`; a deployer-only venv gives error `-32000`. Now a prerequisite step above.)*
</content>
