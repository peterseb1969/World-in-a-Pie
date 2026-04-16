"""Core — minimal API + UI, no OIDC. API-key auth only.

Use when: local dev, demos, or trusted-network installs where OIDC is
overkill. Console is present but unprotected — don't expose publicly.
"""

from typing import Any

CORE: dict[str, Any] = {
    "modules": {"optional": ["console", "mcp-server"]},
    "auth": {"mode": "api-key-only", "gateway": False, "users": []},
    "apps": [],
}
