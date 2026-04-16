"""Full — Everything: UI, OIDC, gateway, reporting, files, ingest.

Use when: production-like deployment with all surfaces enabled. Biggest
footprint (Postgres + MinIO + NATS all active).
"""

from typing import Any

FULL: dict[str, Any] = {
    "modules": {
        "optional": [
            "console",
            "reporting-sync",
            "ingest-gateway",
            "minio",
            "mcp-server",
        ]
    },
    "auth": {"mode": "oidc", "gateway": True},
    "apps": [],
}
