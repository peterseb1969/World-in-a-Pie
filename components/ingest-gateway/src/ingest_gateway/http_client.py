"""HTTP client for forwarding requests to WIP REST APIs."""

import json
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

# A failed answer's detail and body are kept in the outcome only up to these
# sizes, well under NATS's default 1 MB payload.
ERROR_DETAIL_MAX = 500
ERROR_BODY_MAX = 64 * 1024


def _bounded_body(body: Any) -> dict[str, Any] | None:
    """A failed answer's body as the outcome keeps it: whole when it
    serializes within ERROR_BODY_MAX characters, else its start, marked
    truncated. A body that is not a JSON object is kept under "body"."""
    if body is None:
        return None
    kept = body if isinstance(body, dict) else {"body": body}
    text = json.dumps(kept, default=str)
    if len(text) <= ERROR_BODY_MAX:
        return kept
    return {"truncated": True, "raw": text[:ERROR_BODY_MAX]}


# A target's 2xx answer is kept in the outcome whole up to this size of JSON,
# half of NATS's default 1 MB payload so the rest of the outcome always fits.
# A bulk answer over it keeps its counts exact and each item reduced to
# KEPT_ITEM_FIELDS, listed in order while they fit.
OUTCOME_BODY_MAX = 512 * 1024
BULK_COUNT_FIELDS = ("total", "succeeded", "failed")
KEPT_ITEM_FIELDS = ("index", "status", "id", "error_code", "document_id", "version")


def _bounded_answer(body: Any) -> dict[str, Any] | None:
    """A 2xx answer's body as the outcome keeps it: whole within
    OUTCOME_BODY_MAX characters of JSON; beyond it a bulk answer is reduced
    (see _reduced_bulk) and any other body keeps its start, marked truncated.
    A body that is not a JSON object is kept under "body"."""
    if body is None:
        return None
    kept = body if isinstance(body, dict) else {"body": body}
    text = json.dumps(kept, default=str)
    if len(text) <= OUTCOME_BODY_MAX:
        return kept
    results = kept.get("results")
    if isinstance(results, list):
        return _reduced_bulk(kept, results)
    return {"truncated": True, "raw": text[:OUTCOME_BODY_MAX]}


def _reduced_item(item: Any) -> Any:
    """A bulk item cut to the fields a producer needs to tell what happened."""
    if not isinstance(item, dict):
        return item
    return {k: item[k] for k in KEPT_ITEM_FIELDS if k in item}


def _reduced_bulk(body: dict[str, Any], results: list[Any]) -> dict[str, Any]:
    """A bulk answer over the bound: its counts whole, its items reduced and
    listed in order until OUTCOME_BODY_MAX is reached. results_truncated says
    whether items past the last listed one were left out."""
    reduced: dict[str, Any] = {k: body[k] for k in BULK_COUNT_FIELDS if k in body}
    reduced["results_truncated"] = False
    listed: list[Any] = []
    reduced["results"] = listed
    used = len(json.dumps(reduced))
    for item in results:
        small = _reduced_item(item)
        # Two more characters for the ", " that separates list items.
        size = len(json.dumps(small, default=str)) + 2
        if used + size > OUTCOME_BODY_MAX:
            reduced["results_truncated"] = True
            break
        listed.append(small)
        used += size
    return reduced


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
            # Bounded: the outcome is one NATS message, and an outcome too
            # large to publish never reaches its producer.
            detail = response_data.get("detail") if isinstance(response_data, dict) else None
            error_detail = str(detail if detail is not None else response.text)[:ERROR_DETAIL_MAX]
            return IngestResult(
                correlation_id=correlation_id,
                action=action,
                status=IngestResultStatus.FAILED,
                http_status_code=response.status_code,
                response=_bounded_body(response_data),
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
            # Bounded for every 2xx, all-failed ones included: the outcome is
            # one NATS message, and one too large to publish never reaches its
            # producer although the items were written.
            response=_bounded_answer(response_data),
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
