"""HTTP client for forwarding requests to WIP REST APIs."""

import logging
from typing import Any, cast
from urllib.parse import quote

import httpx

from .config import settings
from .models import IngestAction, IngestResult, IngestResultStatus

logger = logging.getLogger(__name__)

# 4xx statuses that mean "not now" rather than "never". 401 is one: an item
# is forwarded only after the gateway identified its key, so a target that
# refuses the key holds an older copy of the keys than the gateway (a key
# created a moment ago reaches each service on its own sync). A key revoked
# meanwhile is refused for good once the deliveries run out.
_TRANSIENT_4XX = frozenset({401, 408, 429})


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
        return cast(str, getattr(settings, attr_name))

    @staticmethod
    def _refused(action: IngestAction, correlation_id: str, error: str) -> IngestResult:
        """A payload that cannot be forwarded as it is (nothing was sent)."""
        return IngestResult(
            correlation_id=correlation_id,
            action=action,
            status=IngestResultStatus.FAILED,
            http_status_code=400,
            error=f"invalid payload: {error}",
        )

    @staticmethod
    def _build_request(
        endpoint_config: dict[str, Any], payload: dict[str, Any]
    ) -> tuple[str, list[Any]] | str:
        """The path and JSON body for a payload, or why it cannot be sent.

        Path parameters (terminology_id for terms.bulk) come out of the
        payload and go into the path as one encoded segment: a raw '?', '#'
        or '/' would re-point the request. Every write endpoint takes a JSON
        array: a create wraps its object; a bulk action sends the list in
        payload[payload_key] (a single object is wrapped). A missing list is
        refused: forwarding [] would report a successful ingest of nothing.
        """
        path = endpoint_config["path"]
        working_payload = payload.copy()  # Don't mutate original
        for param in endpoint_config.get("path_params", []):
            value = working_payload.pop(param, None)
            if not isinstance(value, str) or not value.strip():
                return f"payload.{param} is required: a non-empty string"
            path = path.replace(f"{{{param}}}", quote(value, safe=""))

        payload_key = endpoint_config.get("payload_key")
        if not payload_key:
            return path, [working_payload]
        if payload_key not in working_payload:
            return f"payload.{payload_key} is required: the list of items to write"
        items = working_payload[payload_key]
        return path, items if isinstance(items, list) else [items]

    @staticmethod
    def _outcome(
        action: IngestAction, correlation_id: str, response: httpx.Response
    ) -> IngestResult:
        """The outcome of a target's answer."""
        response_data = None
        try:
            response_data = response.json()
        except Exception:
            response_data = {"raw": response.text[:1000]}

        # Handle HTTP-level errors. A 5xx, 408 or 429 means the target
        # could not take the request right now (starting, overloaded,
        # restarting) — redelivery may succeed. Any other 4xx is a
        # definitive answer about this request (validation, auth,
        # not found) that redelivery cannot change.
        if response.status_code >= 400:
            error_detail = response_data.get("detail") if response_data else response.text[:500]
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                http_status_code=response.status_code,
                response=response_data,
                error=f"HTTP {response.status_code}: {error_detail}",
                retryable=(
                    response.status_code >= 500
                    or response.status_code in _TRANSIENT_4XX
                ),
            )

        # Check BulkResponse for per-item failures.
        # All write endpoints return BulkResponse with {results, total, succeeded, failed}.
        status = IngestResultStatus.SUCCESS
        if response_data and isinstance(response_data, dict):
            failed_count = response_data.get("failed", 0)
            total_count = response_data.get("total", 0)
            if failed_count > 0:
                status = (
                    IngestResultStatus.FAILED
                    if failed_count >= total_count
                    else IngestResultStatus.PARTIAL
                )

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

    async def forward_request(
        self,
        action: IngestAction,
        payload: dict[str, Any],
        correlation_id: str,
        api_key: str,
    ) -> IngestResult:
        """
        Forward a request to the appropriate REST API.

        Args:
            action: The ingest action to perform
            payload: The request payload (REST API body)
            correlation_id: Unique ID for tracking
            api_key: The producer's WIP API key; the target service
                authorizes the write against its grants

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

        built = self._build_request(endpoint_config, payload)
        if isinstance(built, str):
            return self._refused(action, correlation_id, built)
        path, request_body = built
        url = f"{self._get_base_url(endpoint_config['base_url_attr'])}{path}"

        try:
            logger.debug(f"Forwarding {action.value} to {url}")

            response = await self._client.request(
                method=endpoint_config["method"],
                url=url,
                json=request_body,
                headers={
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                },
            )

            return self._outcome(action, correlation_id, response)

        except httpx.TimeoutException as e:
            logger.error(f"Timeout calling {url}: {e}")
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=f"Timeout: {e}",
                retryable=True,
            )
        except httpx.ConnectError as e:
            logger.error(f"Connection error calling {url}: {e}")
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=f"Connection error: {e}",
                retryable=True,
            )
        except httpx.TransportError as e:
            logger.error(f"Transport error calling {url}: {e}")
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=f"Transport error: {e}",
                retryable=True,
            )
        except Exception as e:
            logger.error(f"Error calling {url}: {e}", exc_info=True)
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                error=str(e),
            )
