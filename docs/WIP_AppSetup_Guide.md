# WIP App Setup Guide: How to Prepare a Fresh Claude Session

**Purpose:** Set up a directory so a fresh Claude Code instance can build a WIP-integrated app from scratch, using the documented process (CLAUDE.md, slash commands, MCP resources, reference docs, client library READMEs).

**Validated:** Day 6 — Receipt Scanner built in ~2h15m, zero PoNIF mistakes, zero WIP tutoring from the human. The process works.

---

## Pre-Flight Checklist

### 1. WIP Instance

- [ ] WIP running (standard or full preset) on the target machine
- [ ] Existing data model bootstrapped (if building an app that integrates with existing apps)
- [ ] Some existing data present (the new app may need to reference it)
- [ ] API key available (default dev key: `dev_master_key_for_testing`)
- [ ] All services healthy: Registry (:8001), Def-Store (:8002), Template-Store (:8003), Document-Store (:8004)

### 2. Directory Structure

Create a **fresh directory** — NOT inside an existing app's repo. The new Claude should not have access to other apps' source code. If it needs to understand WIP, that information should be in the documentation, not in example code.

```
my-new-app/
├── .mcp.json                     # MCP server connection (CRITICAL — see below)
├── CLAUDE.md                     # Master instructions (app-specific, see section 7)
├── docs/
│   ├── AI-Assisted-Development.md  # 4-phase process, data model design guide, PoNIFs quick ref
│   ├── WIP_PoNIFs.md              # Full PoNIFs reference (deep context beyond MCP resource)
│   └── WIP_DevGuardrails.md       # UI stack, app skeleton, client library, testing conventions
├── .claude/
│   └── commands/                  # 11 slash commands (copied from WIP repo)
│       ├── explore.md
│       ├── design-model.md
│       ├── implement.md
│       ├── build-app.md
│       ├── improve.md
│       ├── resume.md
│       ├── document.md
│       ├── export-model.md
│       ├── bootstrap.md
│       ├── wip-status.md
│       └── add-app.md
└── libs/
    ├── wip-client-X.Y.Z.tgz     # @wip/client tarball
    ├── wip-client-README.md      # Extracted README (visible without extracting tarball)
    ├── wip-react-X.Y.Z.tgz      # @wip/react tarball
    └── wip-react-README.md       # Extracted README (visible without extracting tarball)
```

**The Claude creates everything else.** No `data-model/`, no `apps/`, no seed files. The existing data model lives in the running WIP instance, discoverable via the `/explore` slash command.

### 3. MCP Server Configuration (CRITICAL)

Claude Code looks for **`.mcp.json`** in the project root. NOT `.claude/mcp_settings.json` — that filename does not work.

Create `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "wip": {
      "command": "/path/to/WorldInPie/.venv/bin/python",
      "args": ["-m", "wip_mcp"],
      "cwd": "/path/to/WorldInPie",
      "env": {
        "WIP_API_KEY": "dev_master_key_for_testing",
        "PYTHONPATH": "/path/to/WorldInPie/components/mcp-server/src"
      }
    }
  }
}
```

**Verify after first launch:** The Claude should see WIP tools (69 tools, 4 resources) when it runs `/mcp`. If the tools aren't available, restart Claude Code — it spawns MCP servers on startup.

### 4. Copying Files from the WIP Repo

The slash commands and reference docs live in the WIP repo. Copy them into the app directory:

```bash
WIP_REPO="/path/to/WorldInPie"
APP_DIR="/path/to/my-new-app"

# Slash commands (canonical source)
mkdir -p "$APP_DIR/.claude/commands"
cp "$WIP_REPO/docs/slash-commands/"*.md "$APP_DIR/.claude/commands/"

# Reference docs
mkdir -p "$APP_DIR/docs"
cp "$WIP_REPO/docs/AI-Assisted-Development.md" "$APP_DIR/docs/"
cp "$WIP_REPO/docs/WIP_PoNIFs.md" "$APP_DIR/docs/"
cp "$WIP_REPO/docs/WIP_DevGuardrails.md" "$APP_DIR/docs/"
```

### 5. Client Libraries

- [ ] Tarballs available (rebuild with `npm pack` from WIP libs directory if READMEs have been updated)
- [ ] **README files extracted** alongside the tarballs in `libs/`:
  ```bash
  tar -xzf libs/wip-client-X.Y.Z.tgz package/README.md -O > libs/wip-client-README.md
  tar -xzf libs/wip-react-X.Y.Z.tgz package/README.md -O > libs/wip-react-README.md
  ```
- [ ] Claude Code can see the READMEs during `/explore` without extracting tarballs

### 6. Files NOT to Include

These files are experiment infrastructure or other apps' code. They add noise without value for a fresh app build:

- No `SETUP_GUIDE.md` — how to set up the constellation infrastructure (not app building)
- No `REPLICATION_GUIDE.md` — same
- No `COMMANDS.md` — redundant with `.claude/commands/` (the commands themselves are the documentation)
- No `LESSONS_LEARNED.md` — meta-learnings about the experiment process, not app building
- No other app's source code

### 7. CLAUDE.md for the App

The app needs its own `CLAUDE.md` — NOT the WIP project's CLAUDE.md. The app's CLAUDE.md should contain:

1. **What this app does** — one paragraph, plain language
2. **The Golden Rule** — "Never modify WIP. Build on top of it."
3. **Process** — "Follow the 4-phase process. Start with `/explore`."
4. **Reference docs** — point to `docs/AI-Assisted-Development.md`, `docs/WIP_PoNIFs.md`, `docs/WIP_DevGuardrails.md`
5. **MCP** — "WIP is accessed exclusively via MCP tools. Read `wip://conventions` and `wip://ponifs` before starting."
6. **Client libraries** — "For Phase 4 (app building), use @wip/client and @wip/react. READMEs are in `libs/`."
7. **WIP connection** — hostname, API key, any app-specific context

Keep it short. The slash commands and reference docs carry the detail.

---

## What the Human Provides

The human is the **domain expert** and the **quality gate**. They provide:

1. **What the app does:** the business problem, the user workflow, the data it handles
2. **Test data:** real files, real formats — the Claude needs to see actual data before designing templates
3. **Scope decisions:** what's in v1, what's deferred, what's the MVP
4. **Domain answers:** field meanings, business rules, edge cases the data doesn't reveal
5. **WIP knowledge when needed:** if the Claude is stuck on a WIP concept, explain it. Don't let documentation gaps slow down the work.

**The important rule: always note the gap.** If the Claude asks something that the documentation *should* have answered, help the Claude (don't block progress), but take a note. After the session, improve the docs so the next Claude doesn't need help on the same thing.

This is how the process actually improved across three rounds:
- **Statement Manager v1** — heavy human intervention on WIP concepts, so many learnings that the app was rebuilt from scratch
- **Statement Manager v2** — less intervention, documentation improved between rounds, more learnings captured
- **Receipt Scanner** — minimal intervention (domain only), documentation nearly sufficient, few new gaps found

Each round made the documentation better. The goal isn't zero human help — it's zero *repeated* human help.

---

## The Process

Tell the fresh Claude to start with:

```
/explore
```

This triggers Phase 1: read CLAUDE.md, read the reference docs, connect to MCP, read `wip://conventions`, `wip://data-model`, and `wip://ponifs`, inventory existing terminologies and templates, report what's there.

Then provide domain knowledge and let the phased process run:
1. `/explore` — what's in WIP already?
2. `/design-model` — map the domain to WIP primitives
3. `/implement` — create terminologies and templates in WIP, verify with test documents
4. `/build-app` — scaffold and build the React/TypeScript application
5. `/improve` — iterate (add features, fix bugs)
6. `/document` — generate README, ARCHITECTURE, etc.

**Supporting commands available at any time:**
- `/wip-status` — check WIP service health and data state
- `/export-model` — save data model to git as seed files
- `/bootstrap` — recreate data model from seed files
- `/add-app` — add a second app that cross-references the first
- `/resume` — recover context after compaction or at the start of a new session

**Compaction warning:** Claude instances are not self-aware about their context usage. When context reaches ~70-80%, **the human should tell the Claude** to run `/resume` or save its state (DESIGN.md, memory files) before compaction hits. The `/resume` command codifies the recovery process so the Claude can rebuild context from durable artifacts (git, WIP state, documentation).

---

## Measurement (Optional)

If running this as an experiment, track:

### Time
- Time from first prompt to `/explore` complete
- Time to Phase 2 gate (data model approved)
- Time to Phase 3 gate (templates created, test documents passing)
- Time to first data on screen (Phase 4)
- Total session time

### Quality
- PoNIF-related mistakes (API calls that fail due to WIP-specific behaviours)
- Times the human had to explain WIP behaviour (should be zero)
- Documentation gaps discovered
- Compilation errors from client library API discovery

### Benchmark (Day 6 Receipt Scanner)

| Metric | Value |
|---|---|
| Total time | ~2h15m (including MCP setup friction) |
| PoNIF mistakes | 0 |
| WIP tutoring from human | 0 |
| MCP resources read first | Yes |
| Compilation errors (client lib) | 9 (all API shape discovery) |
| Pre-compaction state saved | Peter-triggered, executed well |
| Lines of code produced | 3,823 across 56 files |
| Tests | 13 (all passing) |
| Documentation files | 6 |

---

## Known Issues and Workarounds

### MCP Server Not Connecting
Claude Code spawns MCP servers on startup. If the WIP tools aren't available:
1. Check `.mcp.json` exists in the project root (not `.claude/mcp_settings.json`)
2. Verify WIP services are running (`curl http://localhost:8001/health`)
3. Verify the Python module imports (`PYTHONPATH=... python -c "import wip_mcp"`)
4. Restart Claude Code (`/exit` then `claude`)

### page_size Cap
WIP caps `page_size` at 100. The MCP resources and client library READMEs document this, but if the Claude sets `page_size: 200`, it gets a 422 error. Tell the Claude to check `wip://conventions` if this happens.

### Client Library API Discovery
Even with READMEs extracted in `libs/`, the Claude may not read them proactively before writing code. If compilation errors appear related to method signatures, tell the Claude: "Read the README in libs/wip-client-README.md."

### Dev vs Prod API Keys
Apps built against dev mode typically hardcode `dev_master_key_for_testing` in source files, `.env`, or config. A prod deployment (`setup.sh --prod`) generates new random API keys. When switching from dev to prod:
1. Update the API key in each app's `.env` or config file
2. Update the MCP server config (`.mcp.json`) with the new key
3. Re-seed each app's data model via `/bootstrap` (prod deployment starts with an empty database)

---

## Quick Setup Checklist

For the impatient, here's the minimum viable setup:

```bash
# 1. Create app directory
mkdir my-new-app && cd my-new-app
git init

# 2. Copy slash commands from WIP repo
mkdir -p .claude/commands
cp /path/to/WorldInPie/docs/slash-commands/*.md .claude/commands/

# 3. Copy reference docs
mkdir -p docs
cp /path/to/WorldInPie/docs/AI-Assisted-Development.md docs/
cp /path/to/WorldInPie/docs/WIP_PoNIFs.md docs/
cp /path/to/WorldInPie/docs/WIP_DevGuardrails.md docs/

# 4. Copy client libraries
mkdir -p libs
cp /path/to/WorldInPie/libs/wip-client/*.tgz libs/
cp /path/to/WorldInPie/libs/wip-react/*.tgz libs/
tar -xzf libs/wip-client-*.tgz package/README.md -O > libs/wip-client-README.md
tar -xzf libs/wip-react-*.tgz package/README.md -O > libs/wip-react-README.md

# 5. Create .mcp.json (edit paths!)
cat > .mcp.json << 'MCPEOF'
{
  "mcpServers": {
    "wip": {
      "command": "/path/to/WorldInPie/.venv/bin/python",
      "args": ["-m", "wip_mcp"],
      "cwd": "/path/to/WorldInPie",
      "env": {
        "WIP_API_KEY": "dev_master_key_for_testing",
        "PYTHONPATH": "/path/to/WorldInPie/components/mcp-server/src"
      }
    }
  }
}
MCPEOF

# 6. Create CLAUDE.md (see section 7 above for content)
# 7. Launch Claude Code and say: /explore
```

---

*This document was created during the WIP Constellation experiment and updated March 2026 to reflect the information package overhaul (69 MCP tools, 4 resources, 11 slash commands, rewritten reference docs).*
