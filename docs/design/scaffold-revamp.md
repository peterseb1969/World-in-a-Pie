# Agent Scaffold Revamp — Design

**Status:** Analysis complete; target architecture proposed. No implementation yet.
**Last updated:** 2026-07-05
**Author:** Peter + BE-YAC (BE-YAC-20260704-112016)
**See also:**
- `scripts/setup-backend-agent.sh` — current backend scaffold (1,151 lines)
- `scripts/create-app-project.sh` — current app scaffold (1,647 lines)
- `docs/design/wip-deploy-v2.md` — the architectural precedent (spec → config_gen → renderers)

## Context

Two scripts create and maintain YAC working environments:

- `setup-backend-agent.sh` — turns a WIP clone into a BE-YAC environment (venv, `.mcp.json`, CLAUDE.md, slash commands, permissions baseline).
- `create-app-project.sh` — creates or refreshes an APP-YAC project (all of the above minus venv, plus reference docs, client-library tarballs, bootstrap templates, query-preset scaffold, git init).

Both grew by accretion — 40+ `CASE-NNN` incident fixes are encoded as guards, fallback ladders, and preserve-semantics scattered through ~2,800 lines of bash. They work, but every cross-cutting change is now a two-file edit under two different escaping regimes, and the shared blocks have already drifted. This document analyses what the scripts actually do, names the structural problems, and proposes a target architecture.

Same ground rules as wip-deploy v2: solo-user project, clean break allowed, no back-compat machinery. The *behavior* (per-surface policies, incident guards) must carry forward; the *implementation* is disposable.

## What the scripts do today — functional inventory

Both scripts share one skeleton: **guard → parse args → detect mode → resolve tier → provision surfaces → emit content → verify → print next steps.** Mode is never a flag — it is auto-detected from artifact state (backend: `.mcp.json` present ⇒ refresh, CASE-537; app: target dir non-empty ⇒ set-up-in-place, CASE-535).

### Group 1 — Guards & pre-flight
| Concern | backend | app |
|---|---|---|
| Branch guard: develop-only, `ALLOW_NON_DEVELOP` override (CASE-383) | :51-58 | :50-57 — byte-identical |
| MCP pre-flight: WIP venv must import `wip_mcp`, fail loud (CASE-558) | n/a (provisions the venv itself) | :164-184, verify-only by design |
| Source-dir prerequisite check | — | :409-414 |

### Group 2 — Argument parsing & mode detection
Hand-rolled `while/case` in both. Backend: `--target local|ssh|http`, `--host`, `--cert`, `--kb`, `--kb-key`. App: positional dir + `--name`, `--prefix`, `--preset standard|query`, `--force-claude-md`, `--with-bootstrap`, `--kb`, `--kb-key`. The `--kb` URL scheme normalization (CASE-531) is duplicated verbatim.

### Group 3 — Tier resolution + KB enablement
Tier model identical in both: `KB_OPT_IN` = explicit `--kb` this run (drives provisioning); `TIER3` = resulting state (`kb.json` present OR `--kb` given; gates emitted content). `enable_kb()` is a near-duplicate function (~50 lines backend, ~90 app): writes `.claude/kb.json`, fetches the served-client installer with the CASE-557 two-step safety (curl → tempfile → execute only on 2xx + non-empty body), drops the `/wip-case` stub. The app version additionally registers the role prefix as a SESSION_ROLE term via direct def-store POST (CASE-420, :260-295).

### Group 4 — Environment provisioning (backend only)
`find_compatible_python` (3.11–3.13 ladder), venv create, pip/setuptools upgrade, `pytest ruff mypy` seed (:258-302), idempotent `wip_mcp` editable install guarding against half-provisioned venvs (:304-331).

Known gap (hit 2026-07-04): the venv seeds only `pytest ruff mypy` — component test dependencies (pytest-asyncio, httpx, per-component requirements) are not installed, so `wip-test.sh <component>` fails on a fresh scaffold until someone replays the CI install recipe (`.gitea/workflows/test.yaml:113-120`) by hand. The revamp should either provision test deps or make `wip-test.sh` self-provision.

### Group 5 — `.mcp.json` generation
The most drift-prone shared concern. Backend (:333-504): three transports (local stdio / ssh with interactive prompts / http with cert auto-detect); API-key ladder = running-install detection via podman compose `working_dir` label (CASE-521/539) → alphabetical `~/.wip-deploy/*/secrets/api-key` glob → `dev_master_key_for_testing` literal. App (:739-841): stdio only; ladder = `WIP_API_KEY_FILE_OVERRIDE` → preserve existing `.mcp.json` value → podman detection → **hard error** (CASE-558); also preserves a hand-set `WIP_BASE_URL` on refresh (CASE-516).

Two philosophies for the same problem: backend degrades silently to a dev key; app fails loud. That difference is historical accretion, not design — the revamp should pick one (fail loud, per CASE-558's reasoning) and make silent fallback the explicit exception if kept at all.

The podman label-parsing snippet — including the CASE-539 comment about podman 6.0.0 exposing `.Labels` as a comma-joined string — is duplicated character-for-character (:356 / :784).

### Group 6 — CLAUDE.md generation (the elephant)
- Backend: 437-line **quoted** heredoc (:509-946); `__WIP_ROOT__` placeholder substituted by sed; TIER3 regions filtered by sed.
- App: 330-line **unquoted** heredoc (:1203-1533); live `$VAR` interpolation, so every literal backtick and `$` in the prose needs escaping; on refresh renders to `CLAUDE.md.refresh` with a merge notice unless `--force-claude-md` (CASE-418).

This is the gene pool — the agent constitution lives as shell string literals under two different escaping regimes. Prose edits are bash edits: no markdown tooling, no clean diffs, and CLAUDE.md §8 literally instructs agents "the canonical source is the heredoc."

### Group 7 — Slash commands + agent scripts
Both: `rm -f *.md` then `cp docs/slash-commands/{backend|app-builder}/*.md`; tier 2 removes `wip-case.md`; copy `agent-scripts/src/wake_rollover.py` → `.claude/scripts/` (CASE-604). App additionally copies playbooks to flat `docs/playbooks/`, with the CASE-522 rule that `case-workflow.md` is never deleted (WIP-KB serves that path as a load-bearing source).

### Group 8 — Reference docs & bootstrap templates (app only)
Nine docs copied from a hardcoded name list (:660-674) — adding a canonical doc means editing the script. Genesis bootstrap templates (:684-737): SHA+date-stamped "not canonical" banner prepended to each copy (CASE-415), with a deliberate seeding matrix — create: always; refresh: only `--with-bootstrap` AND only when `templates/bootstrap/` is absent (never resurrect a dir a built app deleted per the banner).

### Group 9 — Client libraries + toolkit (app only; hairiest logic)
~170 lines (:843-1013): version-named tarball selection from the lib's `package.json` (CASE-442 immutability doctrine — same filename must mean same bytes), `dist/*.js` content validation, auto-rebuild via `npm install && npm pack`, same-name-different-content hard error, stale-tarball cleanup, README extraction, and on refresh a lockfile sync (`npm install ./libs/*.tgz`) plus a sha512 integrity cross-check between shipped file and `package-lock.json` via node one-liners (:996-997). Then wip-toolkit wheel find-or-build (:1028-1057). Densest CASE history: 441, 442, 495, 520.

### Group 10 — Permissions baseline + hooks
`.claude/settings.json` regenerated every run in both (CASE-446); `.claude/settings.local.json` never touched. Two ~85-line heredocs that a comment claims are "one unified baseline" — **already drifted**: backend carries `mcp__wip-kb__*` allows the app lacks; app carries a `hooks` block (SessionStart/compact) the backend lacks. App also emits `.claude/hooks/post-compact-reanchor.sh` (CASE-480), a third embedded script.

### Group 11 — Identity & metadata markers
Both write `.claude/.session-role` (backend: hardcoded `BE-YAC`; app: `--prefix`, or preserve-with-warning). App persists `.claude/.app-meta` (CASE-418) with a 4-step refresh resolution chain: `.app-meta` → `--name` → CLAUDE.md title/namespace regex backfill (with CASE-460 `|| true` pipefail landmines) → hard error. The backfill parser greps markdown headings and is fragile by admission.

### Group 12 — Runtime provisioning (app create only)
Dev namespace creation via best-effort `POST /api/registry/namespaces` (:1136-1149 — predates and does not use the idempotent `PUT` upsert the platform now documents as canonical); `.env` pointing at the live secrets file rather than a baked key (CASE-495); query-preset scaffold copy with `SCAFFOLD_APP_SLUG`/`SCAFFOLD_APP_NAME` sed substitution, `.gitignore` merge, scaffold-owned CI workflow (CASE-370/489/509).

### Group 13 — Git init & closing UX
App create: gitignore sentinels (`.env`, `.claude/.session-id`, `.claude/.session-role`, `settings.local.json`) + `git init` + initial commit. Both: next-step suggestion keyed on `.claude/.session-id` presence (CASE-532); app: missing-libs ASCII banner.

## Problems

1. **Duplication with active drift.** ~350–400 lines exist in both scripts: branch guard, KB URL normalization, `enable_kb`, podman label parsing, settings.json, TIER3 sed filter, wake-rollover copy, next-step logic. Every CASE fix to a shared block is a two-file edit; nothing enforces it. The settings.json baselines have already diverged despite claiming unification.

2. **Content entangled with code.** ~985 lines of markdown/JSON/shell live inside heredocs under two different escaping regimes (quoted + sed placeholders vs. unquoted + live interpolation). Prose edits are bash edits.

3. **Portability split.** App script uses `sed -i ''` (BSD/macOS-only, :1104-1109, :1540-1542); backend uses portable `sed -i.bak && rm`. `create-app-project.sh` cannot run on Linux today.

4. **Mode interleaving.** Each script braids 2–3 programs (create / refresh / kb-enable) with `if $REFRESH_MODE` at every step, plus `STEP_NUM`/`STEP_OFFSET` arithmetic whose only purpose is renumbering echo output.

5. **The real spec is implicit.** What the scripts actually encode is a per-surface policy matrix (below). That matrix is the design; today it is scattered across 2,800 lines of comments.

6. **Institutional memory as comments.** 40+ CASE references encode why each guard exists. Several guards look redundant until you read their incident (`|| true` under pipefail — CASE-460/534/539). A revamp that ports behavior without the *why* will lose guards to future "cleanup."

## The surface-policy matrix

The core design artifact. Every generated/copied artifact, its owner, and its lifecycle policy — extracted from the current scripts' behavior:

| Surface | Repos | Policy | Guards / provenance |
|---|---|---|---|
| `.venv` + wip_mcp install | BE | create-if-missing; never recreate | idempotent import check (half-provisioned venv) |
| `.mcp.json` | both | regenerate every run; app preserves key-file + base-URL | CASE-516/520/521/539/558; key-file over literal key (CASE-287) |
| `CLAUDE.md` | BE | regenerate every run (scaffold-owned) | TIER3 filter; `__WIP_ROOT__` substitution |
| `CLAUDE.md` | APP | create-only; refresh → `CLAUDE.md.refresh` unless `--force-claude-md` | app-authored content is sacred (CASE-418) |
| `.claude/settings.json` | both | regenerate every run; one baseline, no per-role fork | CASE-446; deny>ask>allow ordering verified |
| `.claude/settings.local.json` | both | never touch | machine/operator-owned |
| `.claude/commands/*.md` | both | wipe + recopy every run | tier 2 removes `wip-case.md` (CASE-463) |
| `.claude/scripts/wake-rollover.py` | both | recopy every run | CASE-604 |
| `.claude/hooks/post-compact-reanchor.sh` | APP (should be both?) | regenerate every run | CASE-480 |
| `.claude/kb.json` | both | write on `--kb` opt-in only; preserve otherwise | user intent, not generated content (CASE-463) |
| `.claude/.session-role` | both | BE: rewrite every run; APP: `--prefix` or preserve+warn | CASE-389 |
| `.claude/.session-id` | both | never written by scaffold (minted by /wip-setup) | local-first identity contract |
| `.claude/.app-meta` | APP | rewrite every run from resolved values | CASE-418 resolution chain |
| `docs/` reference docs | APP | recopy every run (hardcoded 9-doc list) | list should be data, not code |
| `docs/playbooks/*.md` | APP | recopy; **never delete** `case-workflow.md` | CASE-522 (WIP-KB serves it) |
| `templates/bootstrap/*` | APP | create: always; refresh: `--with-bootstrap` AND absent | genesis banner + SHA stamp (CASE-415); never resurrect |
| `libs/*.tgz` + READMEs | APP | recopy; version-named, immutable content | CASE-441/442; integrity check on refresh |
| `package.json`/`package-lock.json` sync | APP | refresh-only; only for deps the app already declares | sha512 cross-check (CASE-442) |
| `libs/wip_toolkit-*.whl` | APP | copy; find-or-build | |
| query-preset scaffold (`server/`, `src/`, Dockerfiles, CI) | APP | create-only, `--preset query` | CASE-370/489/509 |
| `.env` | APP | create-only | key-file pointer, no plaintext (CASE-495) |
| dev namespace | APP | create-only; best-effort | should move to idempotent PUT upsert |
| SESSION_ROLE term registration | APP tier-3 | on `--kb` opt-in; best-effort | CASE-420; durable seed reminder |
| git init + sentinels | APP | create-only | CASE-389 gitignore sentinels |

## Target architecture

Follow the proven in-repo precedent (wip-deploy v2): **spec → shared engine → thin entry points.**

### 1. Content out of code
Move all embedded content to real files under `scaffold/templates/`:

```
scaffold/
├── templates/
│   ├── claude-md/backend.md          # was 437-line heredoc
│   ├── claude-md/app.md              # was 330-line heredoc
│   ├── settings.json                 # ONE baseline (role deltas, if truly needed, as a patch)
│   ├── hooks/post-compact-reanchor.sh
│   └── env/app.env.tmpl
```

One renderer handles the two things the heredocs did: placeholder substitution (`{{WIP_ROOT}}`, `{{APP_NAME}}`, …) and TIER3 region filtering. Prose becomes diffable, lintable markdown with a single escaping regime (none).

### 2. One engine, two thin entry points
A single implementation of the shared logic; `setup-backend-agent.sh` and `create-app-project.sh` remain as entry points (names and CLIs unchanged) but shrink to argument parsing + a role declaration.

**Language: Python.** Rationale: (a) the deployer precedent — spec/config_gen/renderers is already the house idiom; (b) every machine that runs the scaffold has a venv by construction (the backend scaffold makes one, and the app scaffold's CASE-558 pre-flight already requires it); (c) the hairiest current logic — tarball integrity, lockfile sha512 cross-checks, JSON read/patch, label parsing — is one-liner territory in Python and incident-prone in bash/sed/node-inline; (d) it becomes unit-testable under `wip-test.sh` like the deployer (the current scripts have zero tests). Bootstrap ordering note: the backend scaffold's step 1 (venv creation) must stay shell — a thin `setup-backend-agent.sh` creates the venv if missing, then hands off to the Python engine for everything else.

### 3. The matrix as code
The surface-policy table above becomes the engine's declarative core — a list of surface entries `{name, source, dest, roles, policy, tier, case_refs}` with policy ∈ {`regenerate`, `preserve`, `create_only`, `render_refresh`, `opt_in`, `never_touch`}. The engine is one loop over the matrix; per-surface special logic (tarball validation, meta resolution) hangs off entries as handlers. `--dry-run` falls out for free: print what each surface would do.

### 4. Guards as named, tested units
Branch guard, MCP pre-flight, safe-remote-install (CASE-557), running-install detection (CASE-521/539), tarball immutability (CASE-442) — each becomes a function with its CASE reference in the docstring and a unit test pinning the incident behavior. The test suite replaces "grep the comments" as the carrier of institutional memory.

### 5. Behavioral unifications (deliberate changes, not accidents)
- **One settings.json baseline** — end the drift; if BE/APP genuinely need deltas, they're explicit patches on the one baseline.
- **Fail-loud key resolution everywhere** — adopt the app's CASE-558 ladder for the backend too; retire the `dev_master_key_for_testing` silent fallback (or gate it behind an explicit `--dev-fixture` flag).
- **Portable file editing** — no `sed -i` flavor split; the renderer does substitution in-process.
- **Idempotent namespace bootstrap** — switch the dev-namespace create to the documented `PUT` upsert.
- **Hook parity** — decide whether the post-compact re-anchor hook is APP-only by design or an accidental gap in BE (suspected: accidental).
- **Test-dep provisioning** — close the Group-4 gap: either the scaffold seeds component test deps, or `wip-test.sh` self-provisions from the CI recipe.

## Migration plan

Incremental, each step shippable and verifiable against the current scripts' output:

1. **Golden snapshot** — capture current output of both scripts (fresh create + refresh, tier 2 + tier 3) as fixture trees. This is the regression net for everything after.
2. **Extract templates** — move heredoc content to `scaffold/templates/`; scripts `cat`/render them instead of inlining. Pure mechanical move; snapshots must stay byte-identical (modulo the escaping-regime fixes, reviewed by diff).
3. **Build the Python engine** with the surface matrix + renderer + guards, under `wip-test.sh scaffold` unit tests; entry-point scripts delegate surface-by-surface until nothing bash-side remains but the venv bootstrap and arg forwarding.
4. **Apply the deliberate unifications** (settings baseline, fail-loud keys, PUT upsert, hook parity) as separate reviewed commits — never bundled with mechanical moves.
5. **Retire the old bodies**; update CLAUDE.md §8's "canonical source is the heredoc" pointer to the template files.

Validation gate for each step: re-run against a scratch clone and a scratch app dir; diff against the golden snapshots; explain every delta.

## Open questions

1. **Where does the surface matrix live** — Python data structure (simple, typed) or a YAML manifest (deployer-style, but adds a parse layer for a solo-maintained tool)? Leaning Python data structure until a second consumer appears.
2. **Do the two entry-point names survive**, or collapse into one `wip-scaffold backend|app <dir>` CLI? The auto-detection logic (CASE-535/537) transfers either way; collapsing is cleaner but changes muscle memory and docs.
3. **Is the SSH/HTTP transport path still used?** The backend `--target ssh|http` branches (interactive prompts included) look like early-era remnants; if the fleet is all-local now, deleting them removes the only interactive code in either script. Needs Peter's confirmation before dropping.
4. **Should `enable_kb`'s SESSION_ROLE registration move server-side** (KB gateway registers the role on first session mirror) instead of scaffold-side curl-and-parse? Would delete the most fragile network code in the app script; needs an APP-KB case.
5. **App CLAUDE.md refresh model** — `CLAUDE.md.refresh` + manual merge is safe but historically ignored. Worth considering a marked-region model (scaffold-owned sections auto-update between markers, app-authored prose untouched) — strictly better if the marker discipline holds, worse if apps edit inside scaffold regions.
