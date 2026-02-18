"""HTTP client for forwarding requests to WIP REST APIs."""

import logging
from typing import Any

import httpx

from .config import settings
from .models import IngestAction, IngestResult, IngestResultStatus

logger = logging.getLogger(__name__)


# Action to endpoint mapping
ACTION_ENDPOINTS: dict[IngestAction, dict[str, Any]] = {
    IngestAction.TERMINOLOGIES_CREATE: {
        "base_url_attr": "def_store_url",
        "path": "/api/def-store/terminologies",
        "method": "POST",
    },
    IngestAction.TERMS_BULK: {
        "base_url_attr": "def_store_url",
        "path": "/api/def-store/terminologies/{terminology_id}/terms",
        "method": "POST",
        "path_params": ["terminology_id"],
        "payload_key": "terms",  # Extract list from payload["terms"]
    },
    IngestAction.TEMPLATES_CREATE: {
        "base_url_attr": "template_store_url",
        "path": "/api/template-store/templates",
        "method": "POST",
    },
    IngestAction.TEMPLATES_BULK: {
        "base_url_attr": "template_store_url",
        "path": "/api/template-store/templates",
        "method": "POST",
        "payload_key": "templates",  # Extract list from payload["templates"]
    },
    IngestAction.DOCUMENTS_CREATE: {
        "base_url_attr": "document_store_url",
        "path": "/api/document-store/documents",
        "method": "POST",
    },
    IngestAction.DOCUMENTS_BULK: {
        "base_url_attr": "document_store_url",
        "path": "/api/document-store/documents",
        "method": "POST",
        "payload_key": "documents",  # Extract list from payload["documents"]
    },
}


class IngestHTTPClient:
    """HTTP client for calling WIP REST APIs."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    def _get_base_url(self, attr_name: str) -> str:
        """Get base URL from settings attribute."""
        return getattr(settings, attr_name)

    async def forward_request(
        self,
        action: IngestAction,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> IngestResult:
        """
        Forward a request to the appropriate REST API.

        Args:
            action: The ingest action to perform
            payload: The request payload (REST API body)
            correlation_id: Unique ID for tracking

        Returns:
            IngestResult with status and response data
        """
        endpoint_config = ACTION_ENDPOINTS.get(action)
        if not endpoint_config:
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=f"Unknown action: {action}",
            )

        base_url = self._get_base_url(endpoint_config["base_url_attr"])
        path = endpoint_config["path"]

        # Handle path parameters (e.g., terminology_id for terms.bulk)
        # These are extracted from payload and used in the URL
        path_params = endpoint_config.get("path_params", [])
        working_payload = payload.copy()  # Don't mutate original

        for param in path_params:
            if param in working_payload:
                path = path.replace(f"{{{param}}}", str(working_payload.pop(param)))
            else:
                return IngestResult(
                    correlation_id=correlation_id,
                    action=action,
                    status=IngestResultStatus.FAILED,
                    error=f"Missing required path parameter: {param}",
                )

        url = f"{base_url}{path}"

        # Convert payload to bulk format — all write endpoints expect JSON arrays.
        # Single-entity actions: wrap dict as [dict].
        # Bulk actions with payload_key: extract list from payload[key].
        payload_key = endpoint_config.get("payload_key")
        if payload_key:
            request_body = working_payload.get(payload_key, [])
            if not isinstance(request_body, list):
                request_body = [request_body]
        else:
            request_body = [working_payload]

        try:
            logger.debug(f"Forwarding {action.value} to {url}")

            response = await self._client.request(
                method=endpoint_config["method"],
                url=url,
                json=request_body,
                headers={
                    "X-API-Key": settings.api_key,
                    "Content-Type": "application/json",
                },
            )

            # Parse response
            response_data = None
            try:
                response_data = response.json()
            except Exception:
                response_data = {"raw": response.text[:1000]}

            # Handle HTTP-level errors (e.g., 422 validation, 401 auth)
            if response.status_code >= 400:
                error_detail = response_data.get("detail") if response_data else response.text[:500]
                return IngestResult(
                    correlation_id=correlation_id,
                    action=action,
                    status=IngestResultStatus.FAILED,
                    http_status_code=response.status_code,
                    response=response_data,
                    error=f"HTTP {response.status_code}: {error_detail}",
                )

            # Check BulkResponse for per-item failures.
            # All write endpoints return BulkResponse with {results, total, succeeded, failed}.
            status = IngestResultStatus.SUCCESS
            if response_data and isinstance(response_data, dict):
                failed_count = response_data.get("failed", 0)
                total_count = response_data.get("total", 0)
                if failed_count > 0:
                    if failed_count >= total_count:
                        status = IngestResultStatus.FAILED
                    else:
                        status = IngestResultStatus.PARTIAL

            logger.debug(
                f"Forwarded {action.value} correlation_id={correlation_id} "
                f"status={response.status_code}"
            )

            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=status,
                http_status_code=response.status_code,
                response=response_data,
            )

        except httpx.TimeoutException as e:
            logger.error(f"Timeout calling {url}: {e}")
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=f"Timeout: {e}",
            )
        except httpx.ConnectError as e:
            logger.error(f"Connection error calling {url}: {e}")
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=f"Connection error: {e}",
            )
        except Exception as e:
            logger.error(f"Error calling {url}: {e}", exc_info=True)
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=str(e),
            )
