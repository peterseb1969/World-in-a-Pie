"""Standard — Recommended for most users. Everything except ingest-gateway.

Use when: default choice. OIDC via Dex, auth-gateway on. Reporting
(Postgres + reporting-sync), file storage (MinIO), and the MCP server
are all included so the platform is fully exercisable out of the box.
The only optional module not included is `ingest-gateway` — that's
async bulk ingestion via NATS JetStream, opt-in for installs that
specifically want it.

Apps (react-console, etc.) are enabled per-install via `--app`.

Auth: `mode='hybrid'` lets services accept BOTH OIDC bearer JWTs
(for in-browser Console flows via the gateway) AND raw `X-API-Key`
headers (for off-host apps in apps-only / cross-host installs —
CASE-358 + CASE-359 + CASE-374). `mode='oidc'` previously blocked
the API-key path silently — surfaced in CASE-374's live cross-host
test. `hybrid` is strictly more permissive than `oidc` (everything
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
            "minio",
            "mcp-server",
        ]
    },
    "auth": {"mode": "hybrid", "gateway": True},
    "apps": [],
}
