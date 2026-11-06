"""Analytics — Standard + reporting pipeline (Postgres, NATS, Reporting-Sync).

Use when: you need SQL queries / cross-template joins over documents.
Does not include file storage — add `--add files` if you need it.
"""

from typing import Any

ANALYTICS: dict[str, Any] = {
    "modules": {"optional": ["reporting-sync", "mcp-server"]},
    # CASE-367: same shape as CASE-374's fix in standard.py — `oidc`
    # plumbs to WIP_AUTH_MODE=jwt_only, which rejects X-API-Key. Server-
    # side app callers (when apps are added via --app) would fail; flip
    # to `hybrid` so the default works end-to-end. Operators wanting
    # strict OIDC-only posture pass `--auth-mode oidc` explicitly.
    "auth": {"mode": "hybrid", "gateway": True},
    "apps": [],
}
