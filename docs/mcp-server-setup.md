# MCP Server Setup Guide

How to run the WIP MCP server on different hosts. The MCP server provides 69 tools and 4 resources for AI-assisted development against a running WIP instance.

---

## Prerequisites

- WIP services running (all 6 API services must be up)
- Python venv with MCP dependencies installed (handled by `setup.sh`)
- An API key configured in `.env`

Verify services are running:

```bash
curl -s -H "X-API-Key: $YOUR_API_KEY" http://localhost:8001/api/registry/health
```

---

## Option 1: Local stdio (same machine as WIP services)

This is the standard mode for Claude Code, Cursor, Windsurf, etc. running on the same machine as WIP.

### Step 1 — Create `.mcp.json` in the project root

```json
{
  "mcpServers": {
    "wip": {
      "command": "/path/to/World-in-a-Pie/.venv/bin/python",
      "args": ["-m", "wip_mcp"],
      "cwd": "/path/to/World-in-a-Pie",
      "env": {
        "WIP_API_KEY": "your_api_key_here",
        "PYTHONPATH": "/path/to/World-in-a-Pie/components/mcp-server/src"
      }
    }
  }
}
```

Replace `/path/to/World-in-a-Pie` with your actual project directory and `your_api_key_here` with the API key from `.env`.

### Step 2 — Create `start-wip-mcp.sh` convenience script

```bash
#!/bin/bash
cd /path/to/World-in-a-Pie
PYTHONPATH=components/mcp-server/src \
  WIP_API_KEY=your_api_key_here \
  .venv/bin/python -m wip_mcp "$@"
```

Make it executable: `chmod +x start-wip-mcp.sh`

### Step 3 — Verify

Open Claude Code (or your MCP client) in the project directory. The client reads `.mcp.json` and starts the server automatically. You should see WIP tools available (e.g., `get_wip_status`, `list_templates`).

Manual smoke test:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | ./start-wip-mcp.sh 2>/dev/null
```

Expected: JSON response with `serverInfo.name: "wip"`.

---

## Option 2: Remote via SSH (WIP on remote host, Claude Code on local machine)

When WIP runs on a remote host but Claude Code runs on your local machine.

### Step 1 — Set up the remote host

SSH into the host and create the MCP files:

```bash
ssh user@your-wip-host.local
cd ~/World-in-a-Pie
```

Create `start-wip-mcp.sh`:

```bash
#!/bin/bash
cd /home/user/World-in-a-Pie
PYTHONPATH=components/mcp-server/src \
  WIP_API_KEY=your_api_key_here \
  .venv/bin/python -m wip_mcp "$@"
```

```bash
chmod +x start-wip-mcp.sh
```

### Step 2 — Verify the MCP server works on the remote host

```bash
(echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'; \
 sleep 0.5; \
 echo '{"jsonrpc":"2.0","method":"notifications/initialized"}'; \
 sleep 0.5; \
 echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_wip_status","arguments":{}}}'; \
 sleep 3) \
| ./start-wip-mcp.sh 2>/dev/null
```

Expected: two JSON lines — initialize response, then status showing all 6 services healthy.

### Step 3 — Configure Claude Code on your local machine

In your local project's `.mcp.json`, point to the remote host via SSH:

```json
{
  "mcpServers": {
    "wip": {
      "command": "ssh",
      "args": [
        "user@your-wip-host.local",
        "cd /home/user/World-in-a-Pie && PYTHONPATH=components/mcp-server/src WIP_API_KEY=your_api_key_here .venv/bin/python -m wip_mcp"
      ]
    }
  }
}
```

This runs the MCP server on the remote host over SSH stdio — no extra ports needed. SSH handles the transport.

**Requirements:**
- SSH key-based auth to the host (no password prompts)
- `ssh user@your-wip-host.local` must work without interaction

### Step 4 — Verify from local machine

Open Claude Code in the project directory. The MCP tools should appear. Test with `get_wip_status` — it should report all services healthy.

---

## Option 3: SSE mode (network-accessible HTTP server)

For clients that support SSE transport, or when multiple clients need simultaneous access.

### Start the SSE server

```bash
cd /path/to/World-in-a-Pie
PYTHONPATH=components/mcp-server/src \
  WIP_API_KEY=your_api_key_here \
  API_KEY=your_sse_auth_key \
  .venv/bin/python -m wip_mcp --sse
```

The `API_KEY` env var protects the SSE endpoint — clients must send `X-API-Key` header. If omitted, the server runs without auth (prints a warning).

Default: `http://0.0.0.0:8000` (FastMCP defaults).

### Docker

The MCP server has a standalone Dockerfile:

```bash
cd components/mcp-server
podman build -t wip-mcp-server .
podman run -d --name wip-mcp-server \
  --network host \
  -e WIP_API_KEY=your_api_key_here \
  -e API_KEY=your_sse_auth_key \
  wip-mcp-server
```

Using `--network host` so the container can reach WIP services on localhost ports.

---

## Automated Setup

The `setup-backend-agent.sh` script generates `.mcp.json` for all three transport modes:

```bash
# Local stdio (default)
./scripts/setup-backend-agent.sh

# Remote via SSH
./scripts/setup-backend-agent.sh --target ssh --host your-wip-host.local

# HTTP/HTTPS transport
./scripts/setup-backend-agent.sh --target http --host your-wip-host.local
```

---

## Troubleshooting

### "python-dotenv could not parse statement"

Harmless warning from `.env` parsing. The MCP server still works correctly. Caused by multiline values or comments in `.env`.

### Services unreachable

The MCP server defaults to `http://localhost:800X` for all individual services. If you are routing all traffic through the WIP Caddy proxy (the standard deployment approach), set a single base URL:

```bash
export WIP_API_URL=https://localhost:8443
export WIP_VERIFY_TLS=false  # If using a self-signed cert for local dev
```

Alternatively, if services are on a different host without a router, you can configure them individually:

```bash
export REGISTRY_URL=http://your-wip-host.local:8001
export DEF_STORE_URL=http://your-wip-host.local:8002
export TEMPLATE_STORE_URL=http://your-wip-host.local:8003
export DOCUMENT_STORE_URL=http://your-wip-host.local:8004
export REPORTING_SYNC_URL=http://your-wip-host.local:8005
```

### API key issues

The MCP server resolves the API key in order:
1. `WIP_API_KEY` env var
2. Contents of file at `WIP_API_KEY_FILE` path
3. Falls back to `dev_master_key_for_testing`

For production, use a hashed key generated by `scripts/security/generate-api-key.sh`.

### SSH connection drops

If using SSH stdio mode and the connection drops, Claude Code will show the MCP server as disconnected. Ensure:
- SSH `ServerAliveInterval 60` in `~/.ssh/config` for the remote host
- Remote host has `loginctl enable-linger` for persistent user sessions (Linux)
