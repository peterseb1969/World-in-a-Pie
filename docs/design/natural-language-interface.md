# Natural Language Interface Design

**Status:** Planning

## Overview

A web-based conversational interface that lets users query and manage WIP data using natural language. Built on top of the existing MCP server's tool definitions, deployed as an optional WIP component.

The MCP server already provides 30+ tools for AI-assisted data access. Today these are accessible via CLI (Claude Code, Cursor). This component brings the same capability to a web UI — lower barrier to entry, no developer tooling required.

## Goals

1. **Chat with your data** — ask questions, create documents, run reports in plain language
2. **BYOK (Bring Your Own Key)** — users provide their own AI provider API key (Anthropic, Google, OpenAI)
3. **Optional deployment** — WIP works without it; add it when you want conversational access
4. **Minimal new code** — reuse MCP tool definitions, WIP APIs, and existing auth
5. **Pi-friendly** — lightweight backend; all AI inference happens in the cloud

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Natural Language Interface                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Browser                   NLI Service (8007)         WIP Services   │
│                                                                      │
│  ┌──────────┐  1. Message  ┌──────────────┐                         │
│  │ Console  │ ──────────> │  Agent Loop  │                          │
│  │ Chat     │  WebSocket   │              │  2. Tool calls           │
│  │ Panel    │              │  ┌────────┐  │ ───────────────>  :8001  │
│  │          │              │  │Provider│  │                   :8002  │
│  │          │ <────────── │  │  API   │  │ <───────────────  :8003  │
│  │          │  3. Stream   │  └────────┘  │  Tool results    :8004  │
│  │          │   response   │              │                   :8005  │
│  └──────────┘              └──────────────┘                          │
│                                                                      │
│  User's API key travels     Key stored in                            │
│  encrypted over HTTPS       MongoDB (encrypted)                      │
│  or stays in browser         or browser only                         │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Why Not Extend the MCP Server?

The MCP server speaks MCP protocol (stdio/SSE) to AI clients. The NLI service speaks to AI *providers* (Anthropic API, Google AI API) and to *browsers* (WebSocket). Different protocols, different responsibilities:

| | MCP Server | NLI Service |
|---|---|---|
| Talks to | AI clients (Claude Code, Cursor) | AI provider APIs + browsers |
| Protocol | MCP (stdio/SSE) | WebSocket + REST |
| Owns | Tool definitions, WIP API wrappers | Agent loop, conversation state, key management |
| AI model | Chosen by the client | Chosen by the user (BYOK) |

The NLI service **imports tool definitions from the MCP server** (via `tools.yaml` + `_generated_schemas.py`) but calls WIP APIs directly — no MCP-over-MCP indirection.

## Component: NLI Service (`components/nli-service`)

A Python FastAPI service. Thin agent loop, no framework dependencies (no LangChain, no CrewAI).

### Agent Loop

The core loop is simple (~100 lines):

```python
async def run_agent(messages, tools, provider, api_key, on_chunk):
    """
    Execute the agent loop:
    1. Send messages + tool definitions to AI provider
    2. If response contains tool calls, execute them against WIP APIs
    3. Append tool results, repeat from step 1
    4. If response is text-only, stream it to the client and stop
    """
    while True:
        response = await provider.chat(messages, tools, api_key, stream=True)

        # Stream text chunks to client as they arrive
        async for chunk in response:
            if chunk.type == "text":
                await on_chunk(chunk.text)
            elif chunk.type == "tool_call":
                # Execute tool call against WIP API
                result = await execute_tool(chunk.name, chunk.arguments)
                messages.append(tool_call_message(chunk))
                messages.append(tool_result_message(chunk.id, result))

        # If no tool calls were made, we're done
        if not response.has_tool_calls:
            break
```

### Provider Abstraction

```python
class Provider(ABC):
    async def chat(self, messages, tools, api_key, stream) -> AsyncIterator[Chunk]: ...
    def format_tools(self, tool_definitions) -> list: ...

class AnthropicProvider(Provider): ...   # Claude API
class GoogleProvider(Provider): ...      # Gemini API
class OpenAIProvider(Provider): ...      # GPT API (also covers compatible APIs)
```

All three providers support function calling and streaming. The abstraction is thin — just format differences.

### Tool Execution

Tools call WIP APIs directly using the same `WipClient` from the MCP server:

```python
from wip_mcp.client import WipClient

client = WipClient()  # Reads service URLs from env

async def execute_tool(name: str, arguments: dict) -> str:
    """Execute a WIP tool and return the result as a string."""
    handler = TOOL_HANDLERS[name]  # Same handlers as MCP server
    return await handler(**arguments)
```

The tool handler functions can be imported from or shared with the MCP server module, avoiding duplication.

### API Endpoints

```
POST   /api/nli/chat              # Send message, receive streaming response
GET    /api/nli/conversations     # List user's conversations
GET    /api/nli/conversations/:id # Get conversation history
DELETE /api/nli/conversations/:id # Delete conversation
POST   /api/nli/settings          # Save provider + API key
GET    /api/nli/settings          # Get current provider (key masked)
GET    /api/nli/providers         # List supported providers
```

### WebSocket for Streaming

```
WS /api/nli/ws
```

Client sends:
```json
{"type": "message", "content": "Show me all PERSON documents from Switzerland", "conversation_id": "..."}
```

Server streams back:
```json
{"type": "text_delta", "content": "I'll query "}
{"type": "text_delta", "content": "the PERSON documents..."}
{"type": "tool_call", "name": "query_by_template", "arguments": {"template_value": "PERSON", "field_filters": [{"field": "country", "operator": "eq", "value": "CH"}]}}
{"type": "tool_result", "name": "query_by_template", "summary": "Found 23 documents"}
{"type": "text_delta", "content": "I found 23 PERSON documents..."}
{"type": "done"}
```

### Data Model

```python
# Conversation (stored in MongoDB)
{
    "conversation_id": "conv-uuid7",
    "user_id": "admin@wip.local",     # From WIP auth
    "title": "Swiss person query",     # Auto-generated from first message
    "messages": [
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "...", "tool_calls": [...]},
        {"role": "tool", "tool_call_id": "...", "content": "..."},
    ],
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "created_at": "...",
    "updated_at": "..."
}

# User settings (stored in MongoDB)
{
    "user_id": "admin@wip.local",
    "provider": "anthropic",
    "api_key_encrypted": "...",        # AES-256 encrypted
    "model": "claude-sonnet-4-6",
    "created_at": "...",
    "updated_at": "..."
}
```

### API Key Security

Two modes, configurable:

**Server-side storage (default):**
- API key encrypted with AES-256 before storage in MongoDB
- Encryption key from environment: `NLI_ENCRYPTION_KEY`
- Key decrypted per-request, never logged, never cached
- Advantage: key persists across sessions/devices

**Browser-only mode:**
- API key stored in browser localStorage, sent per-request in header
- Server never stores it
- Advantage: zero trust — server never sees the key at rest
- Disadvantage: per-browser, lost on clear

## Component: Console Chat Panel

A chat drawer in the WIP Console, not a separate app.

### UI Design

```
┌─────────────────────────────────────────┐
│  WIP Console                       [💬] │  ← Toggle button in header
├─────────────────────────────────────────┤
│                              ┌─────────┐│
│  [Normal Console Content]    │  Chat   ││
│                              │─────────││
│  Documents, Templates,       │ Hi! How ││
│  Terminologies, etc.         │ can I   ││
│                              │ help?   ││
│                              │         ││
│                              │─────────││
│                              │ [Type…] ││
│                              └─────────┘│
└─────────────────────────────────────────┘
```

- Slide-out drawer on right side (like help panels in VS Code)
- Persists across page navigation
- Shows tool calls inline (collapsible) so the user sees what the AI is doing
- Links in responses navigate to WIP Console pages (e.g., click a document_id to open it)
- Settings gear icon for provider/key configuration

### Vue Components

```
ui/wip-console/src/components/chat/
├── ChatDrawer.vue          # Drawer container, toggle state
├── ChatMessages.vue        # Message list with auto-scroll
├── ChatInput.vue           # Text input with send button
├── ChatToolCall.vue        # Collapsible tool call display
├── ChatSettings.vue        # Provider selection, API key input
└── useChatStore.ts         # Pinia store for conversations + WebSocket
```

### System Prompt

The NLI service prepends a system prompt that includes:
- WIP conventions (from `wip://conventions` resource)
- Data model summary (from `wip://data-model` resource)
- Available tools summary
- User's namespace context

This is the same context the MCP server provides to Claude Code — reused, not rewritten.

## Supported Providers

### Phase 1 (MVP)

| Provider | API | Models | Function Calling |
|----------|-----|--------|-----------------|
| Anthropic | Messages API | Claude Sonnet 4.6, Opus 4.6 | Yes (tools) |

### Phase 2

| Provider | API | Models | Function Calling |
|----------|-----|--------|-----------------|
| Google | Gemini API | Gemini 2.5 Pro/Flash | Yes (function_declarations) |
| OpenAI | Chat Completions | GPT-4o, o3 | Yes (functions) |

The provider abstraction makes adding new providers straightforward — each is ~50 lines mapping to the common interface.

## Deployment

### docker-compose.yml (`components/nli-service/`)

```yaml
services:
  nli-service:
    build: .
    container_name: wip-nli-service
    ports:
      - "8007:8007"
    environment:
      - REGISTRY_URL=${REGISTRY_URL:-http://wip-registry:8001}
      - DEF_STORE_URL=${DEF_STORE_URL:-http://wip-def-store:8002}
      - TEMPLATE_STORE_URL=${TEMPLATE_STORE_URL:-http://wip-template-store:8003}
      - DOCUMENT_STORE_URL=${DOCUMENT_STORE_URL:-http://wip-document-store:8004}
      - REPORTING_SYNC_URL=${REPORTING_SYNC_URL:-http://wip-reporting-sync:8005}
      - MONGODB_URL=${MONGODB_URL:-mongodb://wip:wip@wip-mongodb:27017/wip_nli}
      - NLI_ENCRYPTION_KEY=${NLI_ENCRYPTION_KEY:-dev_encryption_key_for_testing}
      - WIP_AUTH_MODE=${WIP_AUTH_MODE:-dual}
      - WIP_AUTH_LEGACY_API_KEY=${API_KEY}
    networks:
      - wip-network
```

### Optional Deployment

Like Metabase, the NLI service lives in the optional tier:

```
deploy/optional/nli/
├── docker-compose.yml      # Production compose
└── README.md               # Setup instructions
```

`setup.sh` gets a `--with-nli` flag (or module system: `modules: [nli]`).

### Resource Budget (Pi 5, 8GB)

| Resource | Estimate | Notes |
|----------|----------|-------|
| Memory | ~60MB | FastAPI + httpx, no ML models |
| CPU | Negligible idle | Spikes only during tool execution |
| Disk | ~20MB | Python deps + code |
| Network | Depends on usage | AI API calls are the bottleneck |

The NLI service is a proxy — all heavy computation happens at the AI provider.

## Implementation Plan

### Session 1: Backend MVP

1. Create `components/nli-service/` with FastAPI skeleton
2. Implement `AnthropicProvider` with streaming
3. Import tool definitions from MCP server
4. Implement agent loop with tool execution
5. REST endpoint: `POST /api/nli/chat` (non-streaming first)
6. Test: send a message, get a response with tool calls executed

### Session 2: Streaming + Persistence

1. Add WebSocket endpoint with streaming
2. Add conversation storage in MongoDB
3. Add user settings + API key encryption
4. Add conversation management endpoints (list, get, delete)

### Session 3: Console Integration

1. Build `ChatDrawer.vue` with message display
2. Connect to WebSocket, handle streaming
3. Add `ChatSettings.vue` for provider/key setup
4. Add tool call visualization (collapsible blocks)
5. Add document_id/template_id linking to Console pages

### Session 4: Multi-Provider + Polish

1. Add `GoogleProvider` and `OpenAIProvider`
2. Add provider selection in settings UI
3. Conversation title auto-generation
4. Context window management (truncation strategy)
5. Optional deployment setup (`deploy/optional/nli/`)

## What This Is Not

- **Not a general-purpose AI chat.** The system prompt and tools are WIP-specific. This is a data interface, not a chatbot.
- **Not a replacement for the Console.** Bulk operations, template editing, and import workflows are better in structured UI. Chat is for exploration, quick queries, and ad-hoc operations.
- **Not an AI framework.** The agent loop is ~100 lines of straightforward async Python. No chains, no graphs, no agents-calling-agents.

## Open Questions

1. **Context window management.** Long conversations will exceed token limits. Options: (a) truncate old messages, (b) summarize conversation history, (c) just start a new conversation. Leaning toward (c) for MVP — keep it simple.

2. **Rate limiting.** Should the NLI service rate-limit AI API calls to prevent accidental cost spikes? Probably yes — configurable per-user limit.

3. **Conversation sharing.** Can users share conversations? Not in MVP, but the data model supports it (add `shared_with` field).

4. **MCP server reuse vs. import.** Two options for tool handlers: (a) import the MCP server's handler functions directly, (b) copy the WipClient and tool definitions. Option (a) avoids duplication but creates a dependency. Leaning toward (a) — both services are Python, same container network.
