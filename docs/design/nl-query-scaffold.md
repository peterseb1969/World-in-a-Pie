# Natural Language Query Scaffold

**Status:** Ready to implement
**Depends on:** MCP Read-Only Mode (near-term)
**Validated by:** WIP-DnD Compendium (1,384 entities, production-tested)

---

## Goal

Make every new WIP app NL-ready out of the box. A developer runs `create-app-project.sh --preset query` and gets a working natural language interface connected to their WIP data — no AI plumbing required.

---

## What the DnD App Proved

The DnD Compendium is a working NL interface built on WIP. The architecture works well, but the reusable parts are tangled with D&D-specific code. This design extracts the generic mechanics into WIP infrastructure and app scaffolding.

Key findings from the DnD implementation:

| Decision | Outcome |
|----------|---------|
| **Haiku for queries** | Works. Facts live in documents, not the model's head. ~$0.01/query. |
| **Server-side sessions** | Essential. Client-side history would expose `ANTHROPIC_API_KEY`. 30-min TTL, in-memory Map. |
| **Read-only tool filter** | Critical. Without it, "delete all my spells" works. DnD app maintains a client-side allowlist of 23/68 tools — this should be server-side (`WIP_MCP_MODE=readonly`). |
| **Static system prompt** | Works but breaks on compaction. The DnD Claude lost template awareness mid-session. Needs dynamic data model discovery. |
| **23 read-only tools** | Sweet spot. Fewer = can't answer. More = token waste + mutation risk. |
| **Express + MCP stdio** | Simple, reliable. Agent spawns MCP server as child process. No HTTP overhead for tool calls. |
| **Floating chat widget** | Good UX. Non-intrusive, session-persistent, minimal markdown rendering. |

---

## Implementation Plan

### Step 1: `WIP_MCP_MODE=readonly` in MCP Server

Already a separate roadmap item. When `WIP_MCP_MODE=readonly` is set, the MCP server only registers read/query tools. Eliminates the need for client-side tool filtering.

**Read-only tool set** (from DnD app, proven to be sufficient):

```
get_wip_status, search, search_registry,
list_terminologies, get_terminology, get_terminology_by_value,
list_terms, get_term, get_term_hierarchy,
list_templates, get_template, get_template_by_value, get_template_fields,
list_documents, get_document, query_documents, query_by_template,
get_document_versions, get_table_view,
get_file_metadata, list_files,
run_report_query, list_report_tables
```

### Step 2: `describe_data_model` MCP Tool

New MCP tool that returns all templates with their fields, formatted for system prompt injection. Replaces the hardcoded template catalog in DnD's `compendium-assistant.md`.

```
Tool: describe_data_model
Args: namespace (optional)
Returns: Markdown table of all active templates with key fields, term value conventions, query tips
```

The output should be directly pasteable into a system prompt. Example:

```markdown
## Available templates

| Template | Description | Key fields |
|----------|-------------|------------|
| PATIENT | Patient records | name, date_of_birth, blood_type, allergies |
| VISIT | Clinical visits | patient (ref→PATIENT), date, diagnosis, notes |
...

## Query conventions
- Term values are UPPERCASE (e.g., blood_type "A_POSITIVE")
- Use `query_by_template` to filter within a template
- Use `search` for cross-template text search
- Reference fields store entity IDs (use get_document to resolve)
```

This tool calls Template-Store (`list_templates`, `get_template_fields`) and Def-Store (`list_terminologies`) internally, so the agent doesn't need to make multiple round-trips at startup.

### Step 3: `wip://query-assistant-prompt` MCP Resource

New MCP resource that returns a complete, generic system prompt for a WIP query assistant. Combines:

1. Generic instructions (how to use tools, response formatting, query tips)
2. Live output of `describe_data_model` (current template catalog)

Apps read this resource at startup, then append domain-specific instructions:

```typescript
const basePrompt = await mcpClient.readResource('wip://query-assistant-prompt')
const systemPrompt = basePrompt + '\n\n' + appSpecificInstructions
```

This is what `/init-nl-interface` would call under the hood — a forced refresh of the data model snapshot.

### Step 4: `--preset query` in `create-app-project.sh`

New preset that scaffolds a complete NL-ready app. Generated files:

```
my-app/
├── .claude/commands/          # Slash commands (same as today)
├── .mcp.json                  # MCP config with WIP_MCP_MODE=readonly
├── docs/                      # Reference docs (same as today)
├── libs/                      # @wip/client, @wip/react tarballs
├── server/
│   ├── index.ts               # Express server (health, /api/ask)
│   ├── agent.ts               # Claude + MCP agentic loop
│   └── prompts/
│       └── assistant.md       # App-specific prompt additions (starts empty)
├── src/
│   ├── App.tsx                # React shell with router
│   ├── components/
│   │   └── AskBar.tsx         # Floating chat widget
│   ├── lib/
│   │   └── config.ts          # WIP client setup
│   └── pages/
│       └── HomePage.tsx       # Starter page
├── package.json               # All dependencies pre-configured
├── vite.config.ts             # Proxy to WIP services
├── tsconfig.json
├── .env.example               # All required env vars documented
├── CLAUDE.md                  # Starter instructions
└── README.md
```

**`server/agent.ts`** — parameterized version of the DnD agent loop:

```typescript
// Key differences from DnD version:
// - No ALLOWED_TOOLS set (WIP_MCP_MODE=readonly handles this)
// - System prompt loaded from MCP resource, not static file
// - App-specific prompt appended from prompts/assistant.md
// - Model configurable via CLAUDE_MODEL env var
// - Session TTL configurable via SESSION_TTL_MINUTES env var
```

**`src/components/AskBar.tsx`** — generic version of the DnD chat widget:

```typescript
// Key differences from DnD version:
// - No "D&D" or "Compendium" branding
// - Title configurable via prop
// - Placeholder examples configurable via prop
// - Same MarkdownLite renderer
// - Same session management
```

**`.mcp.json`** — includes readonly mode:

```json
{
  "mcpServers": {
    "wip": {
      "command": "...",
      "args": ["-m", "wip_mcp"],
      "env": {
        "WIP_MCP_MODE": "readonly",
        "WIP_API_KEY": "..."
      }
    }
  }
}
```

### Step 5: Architecture Guide

`docs/nl-interface-guide.md` — documents the "why" behind the decisions. Not API docs (those are in READMEs), but architectural rationale:

- Why Haiku over Sonnet (cost vs capability tradeoff for fact-retrieval)
- Why server-side sessions (security, not convenience)
- Why read-only tools (safety, token efficiency)
- Why dynamic system prompts matter (compaction kills static context)
- How to customize: adding domain instructions, changing the model, adjusting session TTL
- How to extend: adding streaming, authentication, multi-user support

---

## Sequence

```
Step 1: WIP_MCP_MODE=readonly          ← MCP server change
Step 2: describe_data_model tool        ← MCP server change (depends on Step 1 only for testing)
Step 3: wip://query-assistant-prompt    ← MCP server change (depends on Step 2)
Step 4: --preset query scaffold         ← create-app-project.sh (depends on Steps 1-3)
Step 5: Architecture guide              ← docs (can be written in parallel)
```

Steps 1-3 are WIP core changes (mcp-server). Step 4 is scaffolding. Step 5 is docs.

---

## What This Does NOT Include

- **No `@wip/agent` library.** The agent loop is scaffolded as owned code, not a dependency. Extract into a library after 3+ apps stabilize the pattern.
- **No streaming.** The DnD app doesn't stream responses. Streaming is a future enhancement (SSE from Express, progressive rendering in AskBar).
- **No multi-user auth.** The scaffold uses a single API key. OIDC integration is a separate concern.
- **No SQL dashboard.** The deterministic query UI is a separate roadmap item that complements the NL interface.
- **No Vue version.** Scaffold is React + Vite. WIP Console is Vue, but new apps use the React scaffold.

---

## Validation

After implementation, this end-to-end test should work:

```bash
# 1. Create a new app
./scripts/create-app-project.sh /tmp/test-app --preset query --name "Test App"

# 2. Set up
cd /tmp/test-app
echo "ANTHROPIC_API_KEY=sk-..." > .env
npm install

# 3. Run (assuming WIP is running with some data)
npm run dev

# 4. Open browser, click chat bubble, ask:
#    "What templates are available?"
#    → Agent calls describe_data_model, returns current template catalog
#    "How many documents exist for each template?"
#    → Agent calls list_templates + query_by_template, returns counts
```

No D&D knowledge required. No hardcoded template names. Works with any WIP dataset.
