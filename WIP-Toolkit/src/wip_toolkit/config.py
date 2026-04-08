"""Configuration resolution for WIP services."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# Default service ports (direct access)
SERVICE_PORTS = {
    "registry": 8001,
    "def-store": 8002,
    "template-store": 8003,
    "document-store": 8004,
    "reporting-sync": 8005,
    "ingest-gateway": 8006,
}

# API path prefixes per service
SERVICE_PREFIXES = {
    "registry": "/api/registry",
    "def-store": "/api/def-store",
    "template-store": "/api/template-store",
    "document-store": "/api/document-store",
    "reporting-sync": "/api/reporting-sync",
    "ingest-gateway": "/api/ingest-gateway",
}

DEFAULT_DEV_API_KEY = "dev_master_key_for_testing"


@dataclass
class WIPConfig:
    """Resolved configuration for connecting to WIP services."""

    host: str = "localhost"
    proxy: bool = False
    proxy_port: int = 8443
    api_key: str = ""
    verify_ssl: bool = True
    verbose: bool = False

    # Computed service URLs
    _service_urls: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = self._resolve_api_key()
        self._service_urls = self._build_service_urls()

    def _resolve_api_key(self) -> str:
        """Resolve API key from environment or .env file."""
        # 1. Environment variable
        key = os.environ.get("WIP_AUTH_LEGACY_API_KEY")
        if key:
            return key

        # 2. .env file in WIP project root (look up from toolkit)
        for search_dir in [Path.cwd(), Path.cwd().parent]:
            env_file = search_dir / ".env"
            if env_file.exists():
                key = _read_env_var(env_file, "WIP_AUTH_LEGACY_API_KEY")
                if key:
                    return key

        # 3. Fall back to dev key
        return DEFAULT_DEV_API_KEY

    def _build_service_urls(self) -> dict[str, str]:
        """Build base URLs for each service."""
        urls = {}
        if self.proxy:
            scheme = "https"
            base = f"{scheme}://{self.host}:{self.proxy_port}"
            for service in SERVICE_PORTS:
                urls[service] = base
        else:
            scheme = "http"
            for service, port in SERVICE_PORTS.items():
                urls[service] = f"{scheme}://{self.host}:{port}"
        return urls

    def service_url(self, service: str) -> str:
        """Get the full base URL for a service (host + prefix)."""
        base = self._service_urls[service]
        prefix = SERVICE_PREFIXES[service]
        return f"{base}{prefix}"


def _read_env_var(env_file: Path, var_name: str) -> str | None:
    """Read a specific variable from a .env file."""
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == var_name:
                value = value.strip().strip("'\"")
                return value if value else None
    except OSError:
        pass
    return None
