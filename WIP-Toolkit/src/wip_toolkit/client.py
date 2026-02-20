"""HTTP client wrapper for WIP services with pagination support."""

from __future__ import annotations

from typing import Any, Iterator

import httpx
from rich.console import Console

from .config import WIPConfig

console = Console(stderr=True)


class WIPClientError(Exception):
    """Raised when a WIP API call fails."""

    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class WIPClient:
    """HTTP client for WIP services with pagination and health checks."""

    def __init__(self, config: WIPConfig) -> None:
        self.config = config
        self._client = httpx.Client(
            headers={"X-API-Key": config.api_key},
            verify=config.verify_ssl,
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WIPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # --- Low-level HTTP ---

    def get(self, service: str, path: str, params: dict | None = None) -> dict:
        url = f"{self.config.service_url(service)}{path}"
        if self.config.verbose:
            console.print(f"[dim]GET {url} {params or ''}[/dim]")
        resp = self._client.get(url, params=params)
        self._check_response(resp)
        return resp.json()

    def post(self, service: str, path: str, json: Any = None, params: dict | None = None) -> dict:
        url = f"{self.config.service_url(service)}{path}"
        if self.config.verbose:
            console.print(f"[dim]POST {url} {params or ''}[/dim]")
        resp = self._client.post(url, json=json, params=params)
        self._check_response(resp)
        return resp.json()

    def put(self, service: str, path: str, json: Any = None) -> dict:
        url = f"{self.config.service_url(service)}{path}"
        if self.config.verbose:
            console.print(f"[dim]PUT {url}[/dim]")
        resp = self._client.put(url, json=json)
        self._check_response(resp)
        return resp.json()

    def post_form(
        self, service: str, path: str,
        data: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict:
        url = f"{self.config.service_url(service)}{path}"
        if self.config.verbose:
            console.print(f"[dim]POST (form) {url}[/dim]")
        resp = self._client.post(url, data=data, files=files)
        self._check_response(resp)
        return resp.json()

    def get_stream(self, service: str, path: str) -> httpx.Response:
        """Get a streaming response (for file downloads)."""
        url = f"{self.config.service_url(service)}{path}"
        if self.config.verbose:
            console.print(f"[dim]GET (stream) {url}[/dim]")
        resp = self._client.get(url)
        self._check_response(resp)
        return resp

    def _check_response(self, resp: httpx.Response) -> None:
        if resp.is_success:
            return
        body = None
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        detail = ""
        if isinstance(body, dict) and "detail" in body:
            detail = f": {body['detail']}"
        elif isinstance(body, str) and len(body) < 200:
            detail = f": {body}"
        raise WIPClientError(
            f"HTTP {resp.status_code} from {resp.url}{detail}",
            status_code=resp.status_code,
            response_body=body,
        )

    # --- Pagination ---

    def fetch_all_paginated(
        self,
        service: str,
        path: str,
        params: dict | None = None,
        page_size: int = 100,
        items_key: str = "items",
    ) -> list[dict]:
        """Fetch all pages of a paginated endpoint."""
        params = dict(params or {})
        params["page_size"] = page_size
        all_items: list[dict] = []
        page = 1

        while True:
            params["page"] = page
            data = self.get(service, path, params)
            page_items = data.get(items_key, [])
            all_items.extend(page_items)
            if len(page_items) < page_size:
                break
            page += 1

        return all_items

    def fetch_paginated_cursor(
        self,
        service: str,
        path: str,
        params: dict | None = None,
        page_size: int = 1000,
        items_key: str = "items",
    ) -> Iterator[list[dict]]:
        """Yield pages of items using cursor-based pagination.

        Each yield is one page (list of dicts). Stops when next_cursor is None
        or the page has fewer items than page_size.
        """
        params = dict(params or {})
        params["page_size"] = page_size
        cursor = None

        while True:
            if cursor:
                params["cursor"] = cursor
            data = self.get(service, path, params)
            page_items = data.get(items_key, [])
            if page_items:
                yield page_items
            next_cursor = data.get("next_cursor")
            if not next_cursor or len(page_items) < page_size:
                break
            cursor = next_cursor

    # --- Health checks ---

    def check_health(self, service: str) -> tuple[bool, str]:
        """Check if a service is healthy. Returns (healthy, message)."""
        try:
            # Health endpoint is at the root, not under the API prefix
            base = self.config._service_urls[service]
            resp = self._client.get(f"{base}/health", timeout=5.0)
            if resp.status_code == 200:
                return True, "healthy"
            return False, f"HTTP {resp.status_code}"
        except httpx.ConnectError:
            return False, "connection refused"
        except httpx.TimeoutException:
            return False, "timeout"
        except Exception as e:
            return False, str(e)

    def check_all_services(self) -> dict[str, tuple[bool, str]]:
        """Check health of all required services."""
        results = {}
        for service in ["registry", "def-store", "template-store", "document-store"]:
            results[service] = self.check_health(service)
        return results
