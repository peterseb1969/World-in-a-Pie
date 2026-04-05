# WIP — Backend Development

## What Is WIP

WIP is a universal template-driven document storage system. It runs on anything from a Raspberry Pi 5 (8GB) to cloud infrastructure. Users define terminologies and templates, then store validated documents against those templates. A reporting pipeline syncs data to PostgreSQL for analytics.

## Getting Started

1. Run `/setup` — verify environment (venv, containers, MCP connectivity)
2. Run `/wip-status` — check service health and data state
3. Run `/roadmap` — see current priorities
4. Run `/understand <component>` — deep-dive into what you're working on

## Essential Reading

- `docs/api-conventions.md` — bulk-first API, BulkResponse contract
- `docs/uniqueness-and-identity.md` — Registry, identity hashing, composite keys
- `docs/development-guide.md` — running tests, quality audit, seed data
- `docs/change-propagation-checklist.md` — what to update when adding/changing fields or features
- `docs/design/ontology-support.md` — term relationships
- MCP resource `wip://ponifs` — 6 non-intuitive behaviours

## Architecture

| Service | Port | Purpose |
|---------|------|---------|
| Registry | 8001 | ID generation, namespace management, synonyms |
| Def-Store | 8002 | Terminologies, terms, aliases, ontology relationships |
| Template-Store | 8003 | Document schemas, field definitions, inheritance, draft mode |
| Document-Store | 8004 | Document CRUD, versioning, term validation, file storage, CSV/XLSX import |
| Reporting-Sync | 8005 | MongoDB → PostgreSQL sync via NATS events |
| Ingest Gateway | 8006 | Async bulk ingestion via NATS JetStream |
| MCP Server | stdio/SSE | 70+ tools, 5 resources for AI-assisted development |
| WIP Console | 8443 | Vue 3 + PrimeVue UI (served via Caddy reverse proxy) |

**Infrastructure:** MongoDB (primary store), PostgreSQL (reporting), NATS JetStream (events), MinIO (files), Caddy (proxy/TLS), Dex (OIDC)

**Libraries:** wip-auth (Python, `libs/wip-auth/`), @wip/client (TypeScript, `libs/wip-client/`), @wip/react (React hooks, `libs/wip-react/`)

See `docs/architecture.md` for full details.

## Key Conventions

- **Bulk-first API:** Every write endpoint accepts `List[ItemRequest]`, returns `BulkResponse`. Always HTTP 200 — errors are per-item. See `docs/api-conventions.md`.
- **Synonym resolution:** APIs accept human-readable synonyms wherever IDs are expected. UUIDs pass through. See `docs/design/universal-synonym-resolution.md`.
- **Stable IDs:** `entity_id` stays the same across versions; `(entity_id, version)` is the unique key. See `docs/uniqueness-and-identity.md`.
- **Namespace-scoped keys:** Non-privileged API keys must declare their namespace scope explicitly. Single-namespace keys enable implicit namespace derivation — the server derives namespace automatically when the caller omits the `namespace` parameter, enabling synonym resolution without `namespace` on every request. Multi-namespace keys must provide `namespace` explicitly.

## Design Principles (Must Follow)

- **The Registry is the identity authority.** The Registry is not just an ID generator — it is the single source of truth for identity. All identity resolution (by canonical ID, synonym, or human-readable value) must go through the Registry via the shared resolution layer (`wip_auth/resolve.py`). Do not implement service-local identity resolution as a substitute (e.g., direct MongoDB value lookups, hardcoded namespace defaults). See `docs/design/synonym-resolution-gaps.md`.
- **Prefer deactivation over deletion.** Soft-delete (`status: inactive`) is the default. Hard deletion is allowed only for: mutable terminology terms, namespace deletion, and binary file cleanup. Do not add new hard-delete paths without explicit design review.
- **References must resolve.** Every entity reference must point to an existing entity. Any valid synonym should work identically to the canonical ID. This is not yet fully implemented — see `docs/design/synonym-resolution-gaps.md` for the current gaps and remediation plan.
- **WIP is guardrails for AI.** WIP's structural constraints (schema validation, controlled vocabularies, referential integrity, versioning) discipline coding agents building applications on top. These constraints must be internally consistent — if a guardrail works sometimes but not always, it's worse than not having it. See `docs/Vision.md` and `docs/WIP_TwoTheses.md`.

## Commands

| Command | Purpose |
|---------|---------|
| `/setup` | First-run environment check and guided setup |
| `/resume` | Recover context after compaction or new session |
| `/wip-status` | Check service health and data state |
| `/understand` | Deep-dive into a component or library |
| `/test` | Run component tests |
| `/quality` | Run quality audit |
| `/review-changes` | Analyze uncommitted work |
| `/pre-commit` | CI-equivalent checks |
| `/roadmap` | Show project priorities |
| `/report` | Capture fireside chat or trigger session summary |

## File Structure

```
World-in-a-Pie/
├── CLAUDE.md                 # This file (generated by setup-backend-agent.sh)
├── docs/                     # Documentation (architecture, APIs, security, design specs)
│   ├── design/               # Feature design documents
│   ├── security/             # Security docs (key rotation, encryption at rest)
│   └── slash-commands/       # Slash command sources (app-builder/, backend/)
├── scripts/                  # Setup, security, quality audit, seed data
├── config/                   # Caddy, Dex, presets, API key configs
├── libs/
│   ├── wip-auth/             # Shared Python auth library
│   ├── wip-client/           # @wip/client TypeScript library
│   └── wip-react/            # @wip/react hooks library
├── components/
│   ├── registry/             # ID & namespace management
│   ├── def-store/            # Terminologies & terms
│   ├── template-store/       # Document schemas
│   ├── document-store/       # Document storage, files, import, replay
│   ├── reporting-sync/       # PostgreSQL sync
│   ├── ingest-gateway/       # Async ingestion via NATS
│   ├── mcp-server/           # MCP server (70+ tools, 5 resources)
│   └── seed_data/            # Test data generation
├── docker-compose/           # Modular compose: base.yml + modules/
├── k8s/                      # Kubernetes manifests
├── ui/wip-console/           # Vue 3 + PrimeVue UI
├── WIP-Toolkit/              # CLI toolkit
├── data/                     # Runtime data (volumes, secrets)
└── testdata/                 # Test fixtures
```

## Git & CI

**Two remotes — always push to both:**
\`\`\`bash
git push origin develop && git push github develop
\`\`\`

- **origin** — `http://gitea.local:3000/peter/World-in-a-Pie.git` (Gitea, primary, runs CI)
- **github** — `git@github.com:peterseb1969/World-in-a-Pie.git` (mirror)

**Branching:** Work on `develop`. `main` is the stable branch (tagged releases only). PRs go to `main` when ready.

**CI:** Gitea Actions via act_runner on `wip-pi.local`. Workflow: `.gitea/workflows/test.yaml`. Runs all component tests. Use `/pre-commit` locally before pushing.

## Working Principles

- **You own what you see.** Multiple AI agents work on this codebase. If you encounter a bug, lint issue, or broken test — fix it. Don't say "another agent should handle this." The user doesn't care who introduced a problem, only that it gets fixed quickly.
- **Don't over-engineer.** Make the minimal change needed. No speculative abstractions, no "while I'm here" refactors.
- **Ask before destructive actions.** Git force-push, dropping data, deleting branches — confirm first.

## Session Awareness

You will be replaced. This session — including everything you learn, every correction Peter makes, every insight you gain — ends when your context fills or the task completes. The next agent starts from scratch with no memory of this conversation.

**Consequence:** Anything worth knowing must be encoded into a durable artifact before this session ends. If Peter corrects your approach, consider whether the correction belongs in:
- A \`/lesson\` entry (quick, structured, for future gene pool review)
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
\`\`\`bash
cat .claude-context-pct
\`\`\`
This file is written to your project directory by the status line. Check it periodically — especially before starting a new subtask.
- **Past 50%:** Ensure your session report and dead ends section are written. You are halfway to replacement.
- **Past 75%:** Stop working and write your session summary. Do not push through hoping to finish — the next YAC picks up faster from a clean summary than from a half-finished sprawl.

When stopping for any reason, write a clear status report: what's done, what's left, what's blocking, and what didn't work (dead ends).

## Critical Gotchas

- **OIDC three-value rule** — issuer URL must match in 3 places. See `docs/network-configuration.md`.
- **Caddy: `handle` not `handle_path`** — services expect the full path. See `docs/network-configuration.md`.
- **Use `./scripts/wip-test.sh <component>` to run tests.** Do not activate venv manually or hand-roll `cd && PYTHONPATH=src pytest` — the wrapper handles venv, paths, and exit codes reliably.
- **Beanie is pinned to `<2.0`.** Do not upgrade without testing `init_beanie()` compatibility. Beanie 2.0+ changes the `init_beanie()` signature and breaks MongoDB initialization.
- **Always activate venv** — `source .venv/bin/activate` before running Python (non-test commands).
- **Container recreate vs restart** — after `.env` changes, `podman-compose down && up -d`, not `restart`.

## YAC Reporting

You are a YAC (Yet Another Claude). You report your work to the Field Reporter by writing files to a shared directory. This reporting is also useful for the *next* YAC — your session reports are input for future agents resuming your work.

**Getting the current time:** Always use `date '+%Y-%m-%d %H:%M'` for timestamps. Do not guess.

**Off the record:** If Peter says "off the record" or "don't report this," skip reporting for that segment. Resume when told.

### Session Identity

At the start of every session, run `date '+%Y%m%d-%H%M'` and assign yourself a session ID:

```
BE-YAC-YYYYMMDD-HHMM
```

Example: `BE-YAC-20260331-1345`.

### Report Directory

Create your report directory at the start of every session:

```bash
mkdir -p /Users/peter/Development/FR-YAC/reports/BE-YAC-YYYYMMDD-HHMM/
```

### Resuming — Check Previous Sessions

At session start (and when running `/resume`), check for recent sessions with your prefix:

```bash
ls -d /Users/peter/Development/FR-YAC/reports/BE-YAC-* 2>/dev/null | tail -1
```

If a previous session exists, read its `session.md` to recover context from the previous agent's work. This is faster and richer than reconstructing from git alone.

If you are continuing work from that session (e.g., after context compaction), add this to your
`session.md` frontmatter:

```
continues: BE-YAC-YYYYMMDD-HHMM
```

### Session Start

Create `session.md` immediately when starting work:

```markdown
---
session: BE-YAC-YYYYMMDD-HHMM
type: backend
repo: World-in-a-Pie
started: YYYY-MM-DD HH:MM
phase: <implement | bugfix | design | test | refactor | docs | other>
tasks:
  - <initial task from user>
---
```

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

### Session Summary

Write the session summary to `session.md` when:
- Peter runs `/report session-end`
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
**Downstream impact:** <changes that may affect apps, MCP tools, client libs, or Console — omit if none>
**Unfinished:** <what's left, if anything>
**For the next YAC:** <context the next agent needs to pick up where you left off>
```

### Fireside Chats

When Peter initiates a design discussion, architecture debate, or scope conversation, use the `/report` slash command to capture it. These are the high-value narrative moments — not just what was decided, but why, what alternatives were considered, and what Peter said.

## Cross-Agent Cases

When you encounter a bug, missing feature, or platform gap that another YAC needs to handle, use the `/case` slash command to file a structured case.

**Shared directory:** `yac-discussions/` (relative to your project root). This is a symlink to the shared case store. If the directory doesn't exist, cross-agent cases are not enabled for this project — tell Peter.

The `/case` command is in `.claude/commands/case.md`. Peter symlinks both the directory and the command into participating projects.

### Quick Reference

- `/case file [optional Peter comment]` — file a new case
- `/case list` — list all open/responded cases (one-line each)
- `/case read <number>` — read a specific case in full
- `/case respond <number>` — append a response to an existing case
- `/case comment <number> [text]` — add a follow-up comment (clarifications, Peter's input, questions)
- `/case close <number>` — close without implementation (won't-fix, deferred, handled manually)
- `/case implement <number>` — apply the proposed patch, then close as implemented
- `/doc-review` — review all open doc-review cases filed by a DOC-YAC (verify accuracy, propose patches)
- `/doc-review <number>` — review a single doc-review case

### When to File

- Bug in a platform component (document-store, registry, MCP server, client libs)
- Missing feature you need (MCP tool, React hook, scaffold capability)
- Platform behavior that contradicts docs or conventions
- Peter tells you to file a case

### When NOT to File

- Bugs in your own app code
- Questions answerable from docs or MCP resources
- Peter said "off the record"
