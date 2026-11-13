"""Standard — Recommended for most users. Everything except ingest-gateway and the app edge.

Use when: default choice. OIDC via Dex, auth-gateway on. Reporting
(Postgres + reporting-sync), file storage (Garage), and the MCP server
are all included so the platform is fully exercisable out of the box.
Two optional modules are not included, each opt-in for installs that
want it: `ingest-gateway` (async bulk ingestion via NATS JetStream) and
`app-edge` (serves apps attached through the Registry's API: `--add
app-edge`, or `wip-deploy add-module app-edge` later).

Apps (react-console, etc.) are enabled per-install via `--app`.

Auth: `mode='hybrid'` lets services accept BOTH OIDC bearer JWTs
(for in-browser Console flows via the gateway) AND raw `X-API-Key`
headers (for apps and scripts that authenticate with a key).
`mode='oidc'` previously blocked the API-key path silently. `hybrid` is strictly more permissive than `oidc` (everything
oidc accepts, plus API keys); operators who want strict JWT-only
opt in with `--auth-mode oidc`.

History: in wip-deploy v1, `standard` included everything but the
ingest-gateway. The semantics were lost in the v2 migration (see
CASE-171); this file restores them. Auth default flipped from `oidc`
to `hybrid` in CASE-374.
"""

from typing import Any

STANDARD: dict[str, Any] = {
    "modules": {
        "optional": [
            "reporting-sync",
            "garage",
            "mcp-server",
        ]
    },
    "auth": {"mode": "hybrid", "gateway": True},
    "apps": [],
}
