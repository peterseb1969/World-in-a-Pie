# NL Query Scaffold — Architecture Guide

The NL Query Scaffold is a full-stack template for building natural-language query interfaces over WIP data. It combines a Claude-powered backend agent (TypeScript/Express) with a React frontend chat widget. The agent connects to WIP's MCP server and uses read-only tools to answer user questions about stored documents, terminologies, and templates.

Create a new project with:

```bash
scripts/create-app-project.sh --preset query
```

## Architecture

```
User → AskBar (React) → POST /api/ask → Express Server → Claude API (agentic loop)
                                                              ↕
                                                    MCP Client → WIP MCP Server → WIP Services
```

## Data Flow

1. User types a question in the AskBar chat widget (floating bottom-right).
2. AskBar POSTs `{ question, sessionId }` to `/api/ask`.
3. Express server calls `ask(question, sessionId)` in `agent.ts`.
4. Agent manages the session (in-memory Map, 30min TTL) and appends the user message.
5. Agent enters an agentic loop (max 15 turns):
   - Calls the Claude API with system prompt + MCP tools + message history.
   - If Claude requests `tool_use`: executes the tool via the MCP client, feeds the result back.
   - If Claude returns `end_turn`: extracts text and returns to the client.
6. Response includes `{ answer, toolCalls, sessionId }`.
7. AskBar displays the answer and a tool-call count badge.

## System Prompt

The system prompt is built dynamically from the `wip://query-assistant-prompt` MCP resource. At read time, the resource calls `_build_data_model_markdown()`, which paginates `list_templates` and `list_terminologies` to embed the live data model (templates, fields, terminologies) into the prompt.

App-specific additions are loaded from `server/prompts/assistant.md` and appended. If WIP is unreachable, the prompt falls back gracefully without the data model section.

## MCP Transport Support

The scaffold supports three MCP transports, controlled by environment variables:

| Transport | Config | Use Case |
|-----------|--------|----------|
| **stdio** (default) | `MCP_PYTHON` + `MCP_MODULE` | Local development — spawns MCP server as subprocess |
| **SSE** | `MCP_URL` + `MCP_TRANSPORT=sse` | Legacy remote — deprecated in MCP spec |
| **Streamable HTTP** | `MCP_URL` (without sse transport) | Modern protocol for K8s/cloud deployments |

## Tool Allowlist

The agent filters MCP tools to a read-only subset (23 tools), defined in the `ALLOWED_TOOLS` Set in `server/agent.ts`.

| Group | Tools |
|-------|-------|
| Status/discovery | `get_wip_status`, `describe_data_model`, `search`, `list_namespaces` |
| Terminologies | `list_terminologies`, `get_terminology`, `list_terms`, `get_term`, `validate_term`, `get_term_hierarchy` |
| Templates | `list_templates`, `get_template`, `get_template_fields` |
| Documents | `list_documents`, `get_document`, `query_documents`, `query_by_template`, `get_document_versions` |
| Reporting | `list_report_tables`, `run_report_query` |
| Files | `get_file_metadata` |

## Namespace Injection

If the `WIP_NAMESPACE` env var is set, the agent auto-injects it into every tool call's `namespace` parameter. This scopes all queries to a single app's data without requiring the user to specify it.

## Session Management

- Sessions are stored in an in-memory Map keyed by `sessionId` (UUID).
- Each session stores the full Anthropic message history for multi-turn conversations.
- TTL: 30 minutes (configurable via `SESSION_TTL_MINUTES`).
- The client round-trips `sessionId` on each request.
- The "New" button in AskBar clears the current session.

## Configuration Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | (required) | Claude API key |
| `MCP_URL` | `''` | Remote MCP server URL |
| `MCP_TRANSPORT` | `''` | `'sse'` or empty (auto-detect) |
| `MCP_PYTHON` | `'python'` | Python binary for stdio transport |
| `MCP_CWD` | cwd | Working directory for MCP subprocess |
| `MCP_MODULE` | `'wip_mcp'` | Python module to run |
| `CLAUDE_MODEL` | `'claude-haiku-4-5-20251001'` | Model for query responses |
| `WIP_API_KEY` | `'dev_master_key_for_testing'` | WIP auth key |
| `WIP_NAMESPACE` | `''` | Scope queries to a single namespace |
| `WIP_MCP_MODE` | `'readonly'` | Injected into MCP subprocess env |
| `MAX_TURNS` | `'15'` | Max agentic loop turns |
| `SESSION_TTL_MINUTES` | `'30'` | Session expiry |
| `PORT` | `'3001'` | Express server port |

## File Structure

```
your-app/
├── server/
│   ├── agent.ts          # Claude + MCP agentic loop
│   ├── index.ts          # Express server, /api/ask endpoint
│   └── prompts/
│       └── assistant.md  # App-specific prompt additions
├── src/
│   ├── App.tsx           # React app with routing
│   ├── main.tsx          # Entry point
│   ├── index.css         # Tailwind imports
│   ├── components/
│   │   └── AskBar.tsx    # Floating chat widget
│   └── pages/
│       └── HomePage.tsx  # Landing page
├── package.json          # Dependencies (anthropic, @modelcontextprotocol/sdk, express, react, vite)
├── vite.config.ts        # Dev server proxy config
├── tailwind.config.js
├── tsconfig.json
├── .env.example
└── index.html
```

## Customization

1. **App-specific knowledge** — Edit `server/prompts/assistant.md`. This content is appended to the system prompt after the data model.
2. **Tool allowlist** — Edit the `ALLOWED_TOOLS` Set in `server/agent.ts` to expose more or fewer MCP tools.
3. **Model choice** — Set `CLAUDE_MODEL`. Haiku for speed, Sonnet for quality.
4. **UI integration** — `AskBar` is a standalone React component. Import it into any page.
5. **Namespace scoping** — Set `WIP_NAMESPACE` to restrict all queries to your app's data.

## Development

```bash
npm run dev          # Runs server + client concurrently
npm run dev:server   # Just the Express+Claude backend
npm run dev:client   # Just the Vite React frontend
npm run build        # Production build
npm start            # Run production server
```
