"""Headless — API only, no UI, no OIDC. Lowest footprint.

Use when: running WIP as a backend for another system, MCP-only workflows,
or CI fixtures. No human-facing surfaces.
"""

from typing import Any

HEADLESS: dict[str, Any] = {
    "modules": {"optional": ["mcp-server"]},
    "auth": {"mode": "api-key-only", "gateway": False, "users": []},
    "apps": [],
}
