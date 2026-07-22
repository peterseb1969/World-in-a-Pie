# __APP_NAME__

<!-- last reviewed: 2026-05-07 -->

## What This App Does

> __APP_NAME__ — TODO: replace this line with what this app does.

## The Golden Rule

> **Never modify WIP. Build on top of it.**

WIP is the backend. This app is a frontend that maps a domain onto WIP's primitives (terminologies, templates, documents) and presents them to users.

**Verify before asserting any factual claim.** Any factual claim a cheap check could falsify — a file's contents, a function's location, a date, a count, a previous case's content — must be checked, not asserted from memory. "I'm pretty sure" is fabrication if you haven't run the check. The pattern has been observed across BE-YAC and FRanC; it is agent-agnostic.

- **Wake-up reading has a quality bar and a ceiling.** A wake-load reading must describe behavior that is **present, current, generally-scoped, and enforced**, with a **reconciliation path** for when reality moves. Five ways it drifts, each with its own fix — name the mode before choosing the fix:
  - **MISSING** — the reading isn't in the wake-load → add it.
  - **STALE** — the reading contradicts reality, with no way to notice → give it a reconciliation path.
  - **TOO-NARROW** — the rule is present but phrased to miss the case → re-scope the wording.
  - **ASPIRATIONAL** — the contract describes intended, not enforced, behavior → back it with a real check.
  - **NOT-RETAINED** — the reading is present, current, scoped, and enforced, and still gets lost to mid-session salience decay under delivery pressure.

  The first four are reading-list fixes. **The fifth is not** — no wake-load change reaches a rule that decays mid-session. It needs an **action-triggered gate**: a check that fires on the risky action itself (the write, the model change), not at the session boundary. When a drift instance appears in the wild, classify it against these five first; if it's NOT-RETAINED, do not reach for a reading-list patch.

**Case numbers in code comments are provenance, never substance.** A comment must state the constraint or invariant in full prose; a `CASE-NNN` token may prefix it as history, but the comment must survive the deletion test: remove the token — does it still explain the code? "See CASE-NNN" as the whole explanation is a dead link to every reader without KB access, and case-pointer comments rot because the pointer never gets re-verified against the code around it. Anything user-facing or generated (UI copy, served API descriptions, docs your app publishes) carries no case tokens at all — those readers have no KB.

## Dev Namespace

Your development namespace is `__DEV_NAMESPACE__`. Use it for all data modeling during development.

**A missing namespace is never a setup failure.** The namespace is created at *bootstrap* — when you actually have data to put in it — not as a setup precondition. `/wip-setup` does not check for it, and its absence at session start is expected and non-blocking. If you have no data model yet (or this app is a cross-namespace console that owns no namespace of its own), there is simply nothing to create now — do **not** treat an absent `__DEV_NAMESPACE__` as an error, fail setup over it, or go hunting for a namespace that was never provisioned.

**Why:** Terminologies and templates are hard to delete cleanly once documents reference them. A dev namespace lets you iterate freely — create, modify, delete, start over — without polluting production data.

**Workflow:**
1. Use `__DEV_NAMESPACE__` for all `/wip-design-model` and `/wip-implement` work
2. Create terminologies, templates, and test documents in this namespace
3. Iterate until the data model is stable
4. When ready for production, create a new namespace (e.g., `__APP_SLUG__`) and recreate the finalized model there
5. Retire the dev namespace via the API when the data model is finalised:
   ```
   mcp__wip__delete_namespace(prefix="__DEV_NAMESPACE__")
   ```
   (or `DELETE /api/registry/namespaces/__DEV_NAMESPACE__`). The API
   honours each namespace's deletion mode (`retain` vs `full`) — no
   `--force` flag needed.

**Important:** MCP tool calls use the privileged admin key, so always pass `namespace=__DEV_NAMESPACE__` explicitly when modeling. Your app's runtime key comes from `WIP_API_KEY_FILE` in `.env` (the wip-deploy secrets file) — see **API Key** below.

## API Key

The MCP server resolves its key from `WIP_API_KEY_FILE` (the live wip-deploy secrets file) — see `.mcp.json`. This is fine for data modeling via MCP tools.

**For your app's runtime API calls**, `.env` carries `WIP_API_KEY_FILE` pointing at that same live file. Resolve the key from the file at startup — the `@wip/proxy` `apiKeyFile` option does this for you, mirroring the MCP server — rather than baking a plaintext key. A key rotation or target-redeploy is then picked up on restart instead of stranding a stale `.env`. This is the deployment's admin/proxy key and spans all namespaces, which a cross-namespace console needs as-is.
`.env` was written with `WIP_API_KEY_FILE=__WIP_API_KEY_FILE__` — no plaintext key is baked in. The runtime reads the file at startup, so rotating the deploy key or redeploying the target needs no edit here.

```bash
# .env (already created)
WIP_API_KEY_FILE=__WIP_API_KEY_FILE__
```

**Least-privilege (optional).** The deploy key is admin-scoped. If this app owns a single namespace and you want a least-privilege key, provision one and repoint `WIP_API_KEY_FILE` at its own secrets file:
```bash
curl -k -X POST https://localhost:8443/api/registry/api-keys \
  -H 'X-API-Key: <admin-key>' -H 'Content-Type: application/json' \
  -d '{"name": "__APP_SLUG__", "namespaces": ["__DEV_NAMESPACE__"], "grant_permission": "write"}'
```

**Multi-namespace key → pass `namespace` explicitly.** The deploy admin/proxy key spans all namespaces, so WIP cannot derive one for you — pass `namespace=__DEV_NAMESPACE__` on API calls that need scoping, or set `defaultNamespace` on `@wip/proxy` to scope reads. A single-namespace key (the least-privilege opt-in above) gets automatic derivation instead.

**Grants:** writes need an explicit namespace grant. If you provision a least-privilege key, pass `grant_permission` on `create_api_key` / `POST /api/registry/api-keys`, or add a grant afterwards (`create_grant` MCP tool, `registry.createGrants` in @wip/client, or `POST /api/registry/namespaces/<ns>/grants`). Grant subject for api keys is the bare key name.

**Key management:** Runtime keys can be listed, updated, and revoked via the Registry API. See WIP's `docs/api-key-management.md` for details.

## The wip-deployable app contract

**Read this before scaffolding any app code:** `docs/wip-deployable-app-contract.md` (bundled into this project by the scaffold). Four-line summary:

1. **Source repo** needs `Dockerfile.dev` + correct `vite.config.ts` (`server.host: '0.0.0.0'`, dev proxy targets *your* Express port, not 3001). Client fetches use `import.meta.env.BASE_URL`, never bare paths.
2. **WIP repo `apps/<name>/wip-app.yaml`** declares both http and dev ports, `WIP_BASE_URL` via `from_component: router`, `APP_BASE_PATH` literal, and a healthcheck that doesn't depend on WIP being reachable.
3. **Verify** with `wip-deploy install --target dev --app <name> --app-source <name>=~/Development/WIP-<name>` — SPA must load at `https://localhost:8443/apps/<name>/` on the first try, container healthy, no manual env patching.
4. **If something breaks**, find the failure signature in the paper's "What breaks when you skip step N" annex. Once `/check-app-deployability` ships, run it before considering your scaffold done.

The contract is target-agnostic — compose, k8s, and apps-only installs satisfy the same contract.

## Process

Follow the 4-phase development process.

If a `KICKOFF.md` exists in this directory, read it first — the kickoff supersedes the standard `/wip-explore` start for special-case apps (e.g. design-package-driven apps like APP-KB).

Otherwise start with:

```
/wip-explore
```

**Core phases** (in order):
1. `/wip-explore` — Read MCP resources, discover existing data model, understand the domain
2. `/wip-design-model` — Map the domain to WIP primitives (user must approve before proceeding)
3. `/wip-implement` — Create terminologies and templates in WIP, verify with test documents
4. `/wip-build-app` — Scaffold and build the React/TypeScript application

**After Phase 4:**
- `/wip-improve` — Iterate (add features, fix bugs, refine UI)
- `/wip-document` — Generate README, ARCHITECTURE, etc.

**Available at any time:**
- `/wip-status` — Check WIP service health and data state
- `/wip-export-model` — Save data model to git as seed files
- `/wip-bootstrap` — Recreate data model from seed files
- `/wip-add-app` — Add a second app that cross-references the first
- `/wip-wake` — Recover context after compaction or at start of a new session
- `/wip-report` — Capture fireside chat or trigger session summary
- `/wip-deploy redeploy|verify` — Redeploy this YAC's own source to the running dev install (or smoke-only). Subset of BE-YAC's `/wip-deploy` — install is BE-YAC's territory
<!--TIER3-->
- `/wip-case file|list|read|respond|comment|close|implement` — Cross-agent case management. **All KB reads/writes go through the served client — never a raw gateway curl:** `kbc kb-write.py <TYPE> …` (writes) / `kbc case-fetch.py …` (reads); the served playbook (`~/.cache/wip-kb-client/case-workflow.md`) is the version-matched source of truth for each verb. The gateway mints the `CASE-<n>` number + synonym and persists edges, but status-transition validity is enforced caller-side and a respond/close/implement is two writes (response doc + `CASE_RECORD --patch status=…`). Cases live in the KB, not on disk — never `Write` a case file with a hand-picked number; never reason about "the next number".
<!--/TIER3-->

**Context management:** When context reaches ~70-80%, the human should tell you to run `/wip-wake` or save state (DESIGN.md, memory files) before compaction hits.

## Namespace Bootstrap on Launch

Every WIP-consuming app must follow the **offer-on-empty / use-on-exists** discipline at runtime. This is a **runtime** discipline (triggered at app launch, when a user is present to act), **not a setup precondition** — a namespace absent at `/wip-setup` time is expected and creates no obligation; it is created here, at bootstrap, only when there is data to put in it. Three rules:

1. **Namespace missing on launch** → show the user an explicit bootstrap offer. Do **not** auto-bootstrap silently. The user can either (a) confirm bootstrap or (b) restore from a backup via the WIP console / `wip-deploy` first and reload.
2. **Namespace exists on launch** → use it as-is. **No** schema reconciliation, **no** "templates differ" check, **no** merge logic. Rolling redeploys against an existing namespace must come up clean. A partially-bootstrapped namespace is the user's signal to use the console, not the app's signal to silently re-bootstrap.
3. **On user-initiated bootstrap** → write one **`<NS_PREFIX>_BOOTSTRAP_RECORD`** audit doc (the template value is namespace-prefixed, e.g. `KB_BOOTSTRAP_RECORD` — derived from your namespace in the server template's `BOOTSTRAP_RECORD_VALUE`; a shared literal value made every app-to-app merge collide on this one template, and the prefix also makes a merged-in record self-labeling about its origin) capturing: `bootstrap_id`, `app_version`, `bootstrapped_at`, `commit_sha`, `templates_created`, `edge_types_created`, `terminologies_created`. This is the provenance trail any future YAC reading the namespace can rely on.

**Restore is not an app concern.** The bootstrap UI mentions restore as an alternative the user may prefer; it does not provide UI for it. Restore is console-initiated.

**Starting point — three template files** are copied into `templates/bootstrap/` of every new app project:
- `bootstrap.server.ts.template` — `checkStatus()` and `runBootstrap()` library functions, with the §3.4 deltas (post-rename term-relations API, BOOTSTRAP_RECORD writing) already applied
- `bootstrap.routes.ts.template` — Express `GET /server-api/bootstrap/status` and `POST /server-api/bootstrap/run` (SSE streaming for progress)
- `BootstrapGate.tsx.template` — React component that wraps the app and renders the four states (checking / unreachable / needs-bootstrap / bootstrapping / error / ready)

Read each template's header comment, fill in the TODO markers (namespace, app title), drop a `<NS_PREFIX>_BOOTSTRAP_RECORD` template into `server/seed/templates/` (value must match the server template's derived `BOOTSTRAP_RECORD_VALUE`), and you're done. The seed-file convention (`server/seed/terminologies/<VALUE>.json`, `server/seed/templates/<NN>_<VALUE>.json`) is documented in the server template's header.

## Reference Documentation

Read these before starting:
- `docs/Vision.md` — WIP's theses and design principles; the drift-correction mechanism when work bends toward a use case at the expense of the generic engine. Read first.
- `docs/AI-Assisted-Development.md` — 4-phase process, data model design guide, PoNIFs quick reference
- `docs/WIP_PoNIFs.md` — Full guide to WIP's 8 non-intuitive behaviours
- `docs/WIP_DevGuardrails.md` — UI stack, app skeleton, testing conventions
- `docs/wip-guide.md` — Operator-facing guide: install, deploy, harden, run alongside an app (consolidates 10 prior docs incl. containerization, auth, networking, storage)
- `docs/technology-stack.md` — **Canonical** v1 stack (React 19 + TS + Vite + TanStack Query + Tailwind 3 + Inter); required @wip/* libraries; forbidden choices. Read before any architecture call.
- `docs/ui-guidance.md` — **Canonical** v1 visual anchor: brand palette tokens (primary/accent/success/danger), typography hierarchy (text-2xl page titles, NOT text-3xl), component shapes (cards, modals, tinted callouts), accessibility floor. `tailwind.config.js` ships pre-extended with these tokens — use the named classes (`bg-primary`, `text-text-muted`), not inline hex.
- `docs/ontology-support.md` — Term relations, polyhierarchy, typed relations, traversal queries
- `docs/wip-deployable-app-contract.md` — what your app must satisfy to ship under `wip-deploy install` (Dockerfile.dev, vite.config, `apps/<name>/wip-app.yaml` ports, WIP-independent healthcheck). Read before scaffolding.
- `templates/bootstrap/*.template` — Bootstrap pattern starting points (see "Namespace Bootstrap on Launch" above)

## Key Identity Concepts

- **Identity hash ≠ canonical ID.** Identity hash = uniqueness key for upsert *within a specific template* — same field values under two different templates are two different documents. Canonical ID / synonyms = deterministic identification of exactly one entity across the entire system (Registry-resolved). When calling `createDocumentsBulk`, the identity hash is scoped to the template you pass — never assume it is unique across templates.
- **The Registry is the identity authority.** All identity resolution goes through the Registry. Do not implement app-side identity resolution by hash lookups — use the document_id returned by the API.
- **WIP's primitives are your only data model — `metadata.*` is a throwaway scratchpad, never a model.** Namespaces, terminologies, terms, templates, documents, files, relationships are the toolkit; if a value needs structure or meaning, it has a home among them. `metadata.custom.<field>` is caller-attached context (loader hints, source-system tags, audit traces) the platform makes no commitments about — NOT a home for anything the platform commits to a meaning for (identity, sortable axes, FTS-indexed text, dedup keys), and NOT a place to persist app state your code reads back. The moment your code branches on metadata, sorts by it, queries it as identity, or treats its shape as a schema, you have built a **sidecar model** — the failure this discipline exists to stop. Logic-driving fields live in `data.<field>` declared on the template's schema, with `identity_fields` / `full_text_indexed` / etc. referencing them; config that matters is a config *document* (create the config template first); a controlled vocabulary is **terms**. If a field your app needs has no home in `data`, that is a design event — file a case asking the template owner (often APP-KB-YAC for the kb namespace, BE-YAC for shared templates) to update the schema; do not stash it in `metadata.custom` as a workaround, and if you're unsure where it belongs, discuss it rather than inventing a shape. The platform hard-rejects `metadata.*` in declarative slots (`identity_fields`, `full_text_indexed`, `sort_by`), but **deliberately leaves the free-form path open**: filters on `POST /documents/query` stay free (ad-hoc reads, not declarative commitments), so the sidecar route is *not* blocked by the platform — the discipline is the guard. Enforced as a checkpoint in `/wip-implement` Step 0 and `/wip-improve` Rule 6.
- **Empty `identity_fields` is a first-class append-only mode**, not a degenerate config. The schema declares the contract: empty list = "every doc is its own logical entity, version-by-document_id-only." Use this deliberately for event logs and audit traces where every write is a fresh entity. Don't use it as a way to skip thinking about identity — if your records have a stable atomic identifier (case_number, ISBN, lot_id, tracking_id), declare it in `data` and reference it in `identity_fields`. **PATCH on an identity-less template fails with `append_only`** — you cannot update a document with no logical identity; create a new one instead. Relatedly, `versioned: false` templates (edge types included) must declare `identity_fields` explicitly (e.g. `[source_ref, target_ref]`) — there is no implicit default.

## MCP

WIP is accessed exclusively via MCP tools (94 tools, 5 resources). Before starting:
- Read `wip://conventions` — bulk-first API, identity hashing, versioning
- Read `wip://data-model` — terminologies, templates, documents, fields, term-relations
- Read `wip://ponifs` — 8 behaviours that trip up every new developer

`wip://development-guide` provides the full 4-phase workflow reference if needed.
`wip://query-assistant-prompt` provides a complete system prompt for NL query agents (used by --preset query apps).

**Query preset — runtime Anthropic key.** The `--preset query` agent resolves its Anthropic key in priority order: a key set at runtime via the admin `/settings` page → `ANTHROPIC_API_KEY_FILE` (0600, survives restart) → `ANTHROPIC_API_KEY` (frozen at process start). So an operator can set/rotate the key from the UI with no redeploy. Two deploy requirements for this to persist: (1) declare `ANTHROPIC_API_KEY_FILE` in `apps/<name>/wip-app.yaml` pointing at a **writable, persistent mount** (otherwise a UI-set key reverts on restart); (2) the `/settings` config endpoint is admin-gated via `ADMIN_GROUPS` (default `wip-admins`) — open only in dev mode (no `OIDC_ISSUER`). The key is a secret: never put it in a WIP document, and the server returns only configured/source/last-4, never the value.

## Client Libraries

For Phase 4 (app building), use @wip/client, @wip/react, and @wip/proxy:
- `libs/wip-client-README.md` — TypeScript client (6 services, error hierarchy, bulk abstraction)
- `libs/wip-react-README.md` — React hooks (TanStack Query, 30+ hooks)
- `libs/wip-proxy-README.md` — Express middleware for WIP API proxying with auth injection

**Phase 4 begins with:**
```bash
npm install ./libs/wip-client-*.tgz ./libs/wip-react-*.tgz ./libs/wip-proxy-*.tgz @tanstack/react-query
```
The WIP libs are tarballs in `libs/`. `@tanstack/react-query` is the peer dependency that powers `@wip/react`'s hooks — install it explicitly; the scaffold's `package.json` does not pre-declare it.

## Dev Setup Gotchas

**TLS:** WIP uses a self-signed cert on whichever hostname the install runs at — `https://localhost:8443` for compose dev, `https://<ingress-hostname>` for k8s (e.g. `https://kb.internal`). Node.js `fetch()` rejects self-signed certs; add `NODE_TLS_REJECT_UNAUTHORIZED=0` to your `dev:server` script (NOT `start`/production). The python wip_mcp client uses `WIP_VERIFY_TLS=false` (already set in `.mcp.json`). Production with proper certs needs no workaround.

**@wip/client baseUrl:** In browser apps behind a Vite proxy, use `baseUrl: '/wip'` (resolved to `window.location.origin + '/wip'`). Do NOT use a bare relative path without the client resolving it — `new URL('/wip/...')` throws without a protocol.

**@wip/react providers:** Hooks require BOTH `QueryClientProvider` (from `@tanstack/react-query`) AND `WipProvider` (from `@wip/react`). Missing either causes silent failure — hooks mount but never fetch, no errors.

## Tool use — Bash timeouts and waits

- **Never set Bash `timeout > 60000` ms.** Use `run_in_background: true` for any command that may exceed 60 s. Use `Monitor` for streaming output, or wait for the auto-completion notification when the background task finishes. A user-scoped PreToolUse hook (`~/.claude/hooks/block-long-bash-timeout.sh`) mechanically rejects calls with `timeout > 60000` — the discipline rule still applies even if the hook is disabled or absent. *Origin: this rule once lived only in feedback memory and still failed to prevent recurrence twice in 90 minutes within one session — hence the mechanical hook.*
- **Verify-before-wait.** Before scheduling any wait on a long-running command, verify the prerequisites that command depends on can succeed. For npm/test runs that hit a backend cluster: check the host-bound port (e.g., `nc -z localhost 8443`) before kicking the wait off. The class of failure is *waiting on an action that depends on unverified state* — the wait then can't complete and burns wall time on a hang. *Origin: an agent once waited 10 minutes for tests that couldn't finish because the deployer no longer exposed the relevant port.*
- **Bash hygiene — don't prefix commands with `cd`.** Your commands already run from the project root, so a `cd` prefix is unnecessary *and* trips approval prompts: `cd "${CLAUDE_PROJECT_DIR:-$PWD}" && …` forces an *expansion* prompt (shell expansion can't be statically verified against the allowlist), and `cd dir && … > file` forces a *path-bypass* prompt (the redirect could land outside an allowlisted path). Both are avoidable — use explicit / relative-to-root paths for reading **and** writing. Keeps inspection and file writes prompt-free *and* safer.

## WIP Toolkit

`wip-toolkit` is a CLI for backup, export, import, and data migration. Install from the wheel in `libs/`:

```bash
pip install libs/wip_toolkit-*.whl
```

Key commands:
- `wip-toolkit export <namespace> <output.zip>` — Export namespace to archive
- `wip-toolkit import <archive.zip> --mode fresh` — Import with new IDs (cross-namespace)
- `wip-toolkit import <archive.zip> --mode restore` — Restore with original IDs (disaster recovery)

Remote WIP instances:
```bash
wip-toolkit --host kb.internal --proxy export kb /tmp/kb-backup.zip
```

## Session Awareness

You will be replaced. This session — including everything you learn, every correction Peter makes, every insight you gain — ends when your context fills or the task completes. The next agent starts from scratch with no memory of this conversation.

**Consequence:** Anything worth knowing must be encoded into a durable artifact before this session ends. If Peter corrects your approach, consider whether the correction belongs in:
- A `/wip-lesson` entry (quick, structured, for future gene pool review)
- A session report "Dead Ends" section (for the next YAC continuing this work)
- A CLAUDE.md update (if Peter agrees it's universal)

Do not say "got it, won't happen again" unless you have written the lesson down. The next agent will make the same mistake unless you leave a trace.

## Scope Budget

Most tasks should complete within a predictable number of commits. If you find yourself significantly exceeding expectations, something is wrong — a misunderstanding, a rabbit hole, or a task that needs decomposition.

**Commit heuristics:**
- A bug fix: 1-3 commits. If you're past 5, stop and report what's blocking you.
- A feature addition: 3-7 commits. If you're past 10, stop and reassess scope with Peter.
- A refactor: 2-5 commits. If you're past 8, you're probably changing too much at once.

**Context window awareness:** You can check your own context usage:
```bash
cat .claude-context-pct
```
This file is written to your project directory by the status line. Check it periodically — especially before starting a new subtask.
- **Past 50%:** Ensure your session report and dead ends section are written. You are halfway to replacement.
- **Past 75%:** Stop working and write your session summary. Do not push through hoping to finish — the next YAC picks up faster from a clean summary than from a half-finished sprawl.

When stopping for any reason, write a clear status report: what's done, what's left, what's blocking, and what didn't work (dead ends).

## YAC Reporting

You are a YAC (Yet Another Claude). You report your work to the Field Reporter by writing files to a shared directory. This reporting is also useful for the *next* YAC — your session reports are input for future agents resuming your work.

**Getting the current time:** Always use `date '+%Y-%m-%d %H:%M'` for timestamps. Do not guess.

**Off the record:** If Peter says "off the record" or "don't report this," skip reporting for that segment. Resume when told.

### Session Identity

Your session ID is minted by `/wip-setup` (fresh start) or `/wip-wake` (continuation after `/clear` or compaction) and stored in `.claude/.session-id`. **Read it; never hand-mint or rotate it** — `cat "$CLAUDE_PROJECT_DIR/.claude/.session-id"`. Those commands also create `reports/<session-id>/`, write the initial `session.md`, and (for `/wip-wake`) auto-close the prior session with `continues_from` linkage.

Your role prefix is read from `.claude/.session-role` (e.g. `APP-KB`), written at scaffold time by `create-app-project.sh --prefix`. **Do not** run `date`-based ID assignment yourself.

The `session.md` these commands create carries this frontmatter — the **local-first identity contract** (`.claude/.session-id` + this frontmatter are authoritative; the kb SESSION record is a derived mirror that catches up on the next reachable write):

```yaml
---
session_id: APP-<X>-YYYYMMDD-HHMMSS
role: APP-<X>
started_at: YYYY-MM-DDTHH:MM:SS
status: active                      # flipped to `closed` by /wip-report session-end or /wip-wake
continues_from: <prior-session-id>  # present only on a /wip-wake continuation
---
```

Seconds precision (`HHMMSS`) eliminates the same-minute collision class. Record the app, phase, and task list in the `session.md` body as you go; don't add a hand-written `continues:` field — `/wip-wake` writes `continues_from` as part of the rollover.

### After Every Commit

Before appending, read `commits.md` first. If the commit hash is already listed, skip it (prevents duplicates after context compaction).

Append to `commits.md` in your report directory:

```markdown
## <short-hash> — <commit message>
**Time:** <run `date '+%H:%M'`>
**Files:** <count> changed, +<added>/-<removed>
**Tests:** <X passed, Y failed — or "not run">
**What:** <1-2 sentences — what changed>
**Why:** <1-2 sentences — what motivated this change>
**PoNIF:** <if you encountered a PoNIF — which one and whether it caused issues. Omit if none.>
**Discovered:** <anything surprising, bugs found, or gaps identified — omit if nothing>
```

If you encountered a PoNIF and handled it correctly, note which one. If you hit a PoNIF and it caused a bug, definitely note it — the Field Reporter tracks these patterns.

### Session Summary

Write the session summary to `session.md` when:
- Peter runs `/wip-report session-end`
- You detect context is running low (~70-80%)
- The session is naturally ending

Update (overwrite) the summary section — don't append multiple summaries.

```markdown
## Session Summary
**Duration:** <start time> – <run `date '+%H:%M'`>
**Commits:** <count>
**Lines:** +<added>/-<removed>
**Phase:** <which phase(s) you worked in>
**What happened:** <3-5 sentences covering the session's arc — not a commit list, but the narrative>
**WIP interactions:** <any platform bugs, missing MCP tools, or upstream issues discovered — omit if none>
**Unfinished:** <what's left, if anything>
**For the next YAC:** <context the next agent needs to pick up where you left off>
```

### Fireside Chats

When Peter initiates a design discussion, architecture debate, or scope conversation, use the `/wip-report` slash command to capture it. These are the high-value narrative moments — not just what was decided, but why, what alternatives were considered, and what Peter said.

### Running Log

For session-meaningful work that is **neither a change, an end-state, nor a fireside-grade decision**, append to `session-updates.md` via `/wip-report update-session [terse note]`. Three trigger categories:

1. **Discoveries without a commit anchor** — e.g., "scaffold imports `./wip-api.js` which doesn't exist anywhere."
2. **Scope-trim decisions mid-session** — why you're doing less than originally pitched, when the rationale matters for reading the resulting commit but isn't architectural enough for a fireside.
3. **Block/unblock state and pre-`/compact` snapshots** — written when context is filling so the post-compaction same-agent self has more than just the last commit message and a stale session.md.

**`/compact` vs `/clear`:** before `/compact` (same agent continues, conversation just summarized) write a running-log entry — this mode. Before `/clear` (next agent starts cold from durable artifacts) run `/wip-report session-end`. The two events look similar but have different recovery semantics. A session is bounded by context usage, not by the calendar: it does not end because a day ended or because the human stopped for the night — a session ID several days old means the context lasted, which is the good outcome. `/clear` is the human's call, made when the window nears full; never propose it on a schedule.

Append-only — distinct from `session.md` (overwritten at end) and `report-<slug>.md` (per-decision). Each entry is **timestamp + short headline + one paragraph**.

Discipline test before writing: *"Would future-me, after a compaction, want to know this in 6 hours?"* If yes, write. If "this is just thinking out loud," don't.

The four files together — `session.md` + `commits.md` + `session-updates.md` + any `report-*.md` — are what `/wip-wake` reads to rebuild context.
