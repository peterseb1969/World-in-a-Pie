"""Standard — Recommended for most users. API + OIDC + gateway.

Use when: default choice. OIDC via Dex, auth-gateway on. No reporting
or file storage (add separately if needed). Apps (react-console, etc.)
are enabled per-install via `--app`.
"""

from typing import Any

STANDARD: dict[str, Any] = {
    "modules": {"optional": ["mcp-server"]},
    "auth": {"mode": "oidc", "gateway": True},
    "apps": [],
}
