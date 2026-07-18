"""Core — minimal API, no OIDC. API-key auth only.

Use when: local dev, demos, or trusted-network installs where OIDC is
overkill. Apps (react-console, etc.) are opt-in via `--app`.
"""

from typing import Any

CORE: dict[str, Any] = {
    "modules": {"optional": ["mcp-server"]},
    "auth": {"mode": "api-key-only", "gateway": False, "users": []},
    "apps": [],
}
