"""Standard — Recommended for most users. UI + OIDC + Theme 7 gateway.

Use when: default choice. Console protected by the auth-gateway, OIDC
via Dex, no reporting/file storage (add separately if needed).
"""

from typing import Any

STANDARD: dict[str, Any] = {
    "modules": {"optional": ["console", "mcp-server"]},
    "auth": {"mode": "oidc", "gateway": True},
    "apps": [],
}
