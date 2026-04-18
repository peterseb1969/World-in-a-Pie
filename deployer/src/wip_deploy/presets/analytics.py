"""Analytics — Standard + reporting pipeline (Postgres, NATS, Reporting-Sync).

Use when: you need SQL queries / cross-template joins over documents.
Does not include file storage — add `--add files` if you need it.
"""

from typing import Any

ANALYTICS: dict[str, Any] = {
    "modules": {"optional": ["reporting-sync", "mcp-server"]},
    "auth": {"mode": "oidc", "gateway": True},
    "apps": [],
}
