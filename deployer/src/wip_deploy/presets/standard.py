"""Standard — Recommended for most users. Everything except ingest-gateway.

Use when: default choice. OIDC via Dex, auth-gateway on. Reporting
(Postgres + reporting-sync), file storage (MinIO), and the MCP server
are all included so the platform is fully exercisable out of the box.
The only optional module not included is `ingest-gateway` — that's
async bulk ingestion via NATS JetStream, opt-in for installs that
specifically want it.

Apps (react-console, etc.) are enabled per-install via `--app`.

History: in wip-deploy v1, `standard` included everything but the
ingest-gateway. The semantics were lost in the v2 migration (see
CASE-171); this file restores them.
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
    "auth": {"mode": "oidc", "gateway": True},
    "apps": [],
}
