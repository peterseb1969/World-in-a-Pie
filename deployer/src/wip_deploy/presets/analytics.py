"""Analytics — Standard without file storage.

Use when: you need SQL queries / cross-template joins over documents and no
object store. Its optional services are `standard`'s (reporting-sync,
mcp-server) less Garage — reporting-sync still brings Postgres and NATS as
its own infrastructure here, exactly as it does in `standard`.

Need file storage as well? That is what separates `standard` (and `full`)
from this preset: use one of them, or add the store afterwards with
`--add garage` / `wip-deploy add-module garage`.
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
