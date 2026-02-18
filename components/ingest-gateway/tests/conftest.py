"""Shared fixtures for ingest gateway tests."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

from ingest_gateway.models import IngestAction, IngestResult, IngestResultStatus
from ingest_gateway.http_client import IngestHTTPClient
from ingest_gateway.worker import IngestWorker


@pytest.fixture
def http_client():
    """Create IngestHTTPClient with mocked internal httpx client."""
    client = IngestHTTPClient()
    client._client = MagicMock()
    return client


@pytest.fixture
def mock_http_client():
    """Mock HTTP client for worker tests."""
    client = MagicMock(spec=["forward_request", "close"])
    client.forward_request = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_result_publisher():
    """Mock result publisher for worker tests."""
    pub = MagicMock(spec=["publish"])
    pub.publish = AsyncMock(return_value=True)
    return pub


@pytest.fixture
def worker(mock_http_client, mock_result_publisher):
    """Create IngestWorker with mocked dependencies."""
    nc = MagicMock()
    js = MagicMock()
    return IngestWorker(nc, js, mock_http_client, mock_result_publisher)


def make_mock_response(status_code=200, json_data=None, text=""):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("No JSON body")
    return resp


def make_bulk_response(results, total=None, succeeded=None, failed=None):
    """Create a BulkResponse dict."""
    if total is None:
        total = len(results)
    if succeeded is None:
        succeeded = sum(1 for r in results if r.get("status") != "error")
    if failed is None:
        failed = sum(1 for r in results if r.get("status") == "error")
    return {
        "results": results,
        "total": total,
        "succeeded": succeeded,
        "failed": failed,
    }
