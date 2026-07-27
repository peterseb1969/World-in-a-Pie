"""Client for communicating with the WIP Document-Store service.

Used exclusively for ADVISORY data: the create-as-upsert impact analysis
asks document-store how many live documents a version event affects. Every
caller must treat a failure here as "impact unavailable", never as an error
that blocks the template write — a template-store that cannot reach
document-store still versions templates; it just says so instead of
reporting a silent zero (a silent zero reads as "no documents affected",
which is the misleading case).
"""

import os
from typing import Any, cast

import httpx


class DocumentStoreClient:
    """Read-only client for the Document-Store impact-stats endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 5.0,
    ):
        self.base_url = base_url or os.getenv(
            "DOCUMENT_STORE_URL",
            "http://localhost:8004"
        )
        self.api_key = cast(str, api_key or os.getenv(
            "DOCUMENT_STORE_API_KEY",
            "dev_master_key_for_testing"
        ))
        self.timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def get_impact_stats(
        self,
        template_id: str,
        namespace: str,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch per-version live-doc counts + per-field non-empty counts.

        Raises on any transport or HTTP failure — the caller converts that
        into the explicit "unavailable" marker.
        """
        params: dict[str, str] = {"template_id": template_id, "namespace": namespace}
        if fields:
            params["fields"] = ",".join(fields)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/api/document-store/documents/impact-stats",
                headers=self._get_headers(),
                params=params,
            )
            response.raise_for_status()
            return cast(dict[str, Any], response.json())


# ── Singleton ───────────────────────────────────────────────────────────────


_client: DocumentStoreClient | None = None


def get_document_store_client() -> DocumentStoreClient:
    """Get the singleton Document-Store client instance."""
    global _client
    if _client is None:
        _client = DocumentStoreClient()
    return _client


def set_document_store_client(client: DocumentStoreClient | None) -> None:
    """Inject or reset the singleton (tests)."""
    global _client
    _client = client
