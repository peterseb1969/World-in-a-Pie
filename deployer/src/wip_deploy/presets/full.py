"""Full — Everything: OIDC, gateway, reporting, files, ingest.

Use when: production-like deployment with all surfaces enabled. Biggest
footprint (Postgres + MinIO + NATS all active). Apps (react-console,
etc.) are enabled per-install via `--app`.
"""

from typing import Any

FULL: dict[str, Any] = {
    "modules": {
        "optional": [
            "reporting-sync",
            "ingest-gateway",
            "minio",
            "mcp-server",
        ]
    },
    # CASE-367: same shape as CASE-374's fix in standard.py — `oidc`
    # plumbs to WIP_AUTH_MODE=jwt_only, which rejects X-API-Key. Apps
    # making server-side platform calls (react-console, wip-kb) fail
    # silently after a default install. `hybrid` accepts both JWTs
    # (gateway-forwarded request path) AND API keys (server-side app
    # code). Operators wanting strict OIDC-only posture pass
    # `--auth-mode oidc` explicitly.
    "auth": {"mode": "hybrid", "gateway": True},
    "apps": [],
}
