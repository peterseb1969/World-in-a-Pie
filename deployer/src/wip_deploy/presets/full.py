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
    "auth": {"mode": "oidc", "gateway": True},
    "apps": [],
}
