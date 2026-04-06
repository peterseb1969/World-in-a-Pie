"""Tests for WIPClient HTTP wrapper."""

from unittest.mock import MagicMock, patch, PropertyMock

import httpx
import pytest

from wip_toolkit.client import WIPClient, WIPClientError
from wip_toolkit.config import WIPConfig


@pytest.fixture
def config():
    """Create a test config pointing at localhost."""
    return WIPConfig(
        host="localhost",
        proxy=False,
        api_key="test_api_key",
        verify_ssl=False,
        verbose=False,
    )


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx.Client."""
    with patch("wip_toolkit.client.httpx.Client") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        yield mock_instance


def _make_response(status_code=200, json_data=None, text="", url="http://localhost:8001/test"):
    """Helper to create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 400
    resp.url = url
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.return_value = {}
    return resp


class TestWIPClientGet:
    """Test GET requests."""

    def test_get_returns_json(self, config, mock_httpx_client):
        expected = {"items": [{"id": "1"}], "total": 1}
        mock_httpx_client.get.return_value = _make_response(json_data=expected)

        client = WIPClient(config)
        result = client.get("registry", "/namespaces")

        assert result == expected
        mock_httpx_client.get.assert_called_once()
        call_args = mock_httpx_client.get.call_args
        assert "/api/registry/namespaces" in call_args[0][0]

    def test_get_with_params(self, config, mock_httpx_client):
        mock_httpx_client.get.return_value = _make_response(json_data={"items": []})

        client = WIPClient(config)
        client.get("def-store", "/terminologies", params={"namespace": "wip", "status": "active"})

        call_args = mock_httpx_client.get.call_args
        assert call_args[1]["params"] == {"namespace": "wip", "status": "active"}


class TestWIPClientPost:
    """Test POST requests."""

    def test_post_with_json_body(self, config, mock_httpx_client):
        payload = {"value": "COUNTRY", "namespace": "wip"}
        expected = {"results": [{"status": "created", "id": "0190a000-0000-7000-0000-000000000001"}]}
        mock_httpx_client.post.return_value = _make_response(json_data=expected)

        client = WIPClient(config)
        result = client.post("def-store", "/terminologies", json=[payload])

        assert result == expected
        call_args = mock_httpx_client.post.call_args
        assert "/api/def-store/terminologies" in call_args[0][0]
        assert call_args[1]["json"] == [payload]

    def test_post_with_params(self, config, mock_httpx_client):
        mock_httpx_client.post.return_value = _make_response(json_data={"results": []})

        client = WIPClient(config)
        client.post("document-store", "/documents", json=[], params={"continue_on_error": "true"})

        call_args = mock_httpx_client.post.call_args
        assert call_args[1]["params"] == {"continue_on_error": "true"}


class TestWIPClientErrorHandling:
    """Test error handling for failed HTTP responses."""

    def test_4xx_raises_client_error(self, config, mock_httpx_client):
        mock_httpx_client.get.return_value = _make_response(
            status_code=404,
            json_data={"detail": "Namespace not found"},
        )

        client = WIPClient(config)
        with pytest.raises(WIPClientError) as exc_info:
            client.get("registry", "/namespaces/nonexistent")

        assert exc_info.value.status_code == 404
        assert "404" in str(exc_info.value)
        assert "Namespace not found" in str(exc_info.value)

    def test_5xx_raises_client_error(self, config, mock_httpx_client):
        mock_httpx_client.post.return_value = _make_response(
            status_code=500,
            json_data={"detail": "Internal server error"},
        )

        client = WIPClient(config)
        with pytest.raises(WIPClientError) as exc_info:
            client.post("def-store", "/terminologies", json=[])

        assert exc_info.value.status_code == 500

    def test_error_preserves_response_body(self, config, mock_httpx_client):
        body = {"detail": "Validation error", "errors": ["field required"]}
        mock_httpx_client.get.return_value = _make_response(
            status_code=422,
            json_data=body,
        )

        client = WIPClient(config)
        with pytest.raises(WIPClientError) as exc_info:
            client.get("registry", "/namespaces/bad")

        assert exc_info.value.response_body == body

    def test_error_with_text_body(self, config, mock_httpx_client):
        resp = _make_response(status_code=502, text="Bad Gateway")
        resp.json.side_effect = ValueError("Not JSON")
        mock_httpx_client.get.return_value = resp

        client = WIPClient(config)
        with pytest.raises(WIPClientError) as exc_info:
            client.get("registry", "/health")

        assert exc_info.value.status_code == 502
        assert "Bad Gateway" in str(exc_info.value)

    def test_success_does_not_raise(self, config, mock_httpx_client):
        mock_httpx_client.get.return_value = _make_response(
            status_code=200,
            json_data={"ok": True},
        )

        client = WIPClient(config)
        result = client.get("registry", "/namespaces")
        assert result == {"ok": True}


class TestFetchAllPaginated:
    """Test paginated fetching across multiple pages."""

    def test_single_page(self, config, mock_httpx_client):
        """When results fit in one page, only one request is made."""
        page_data = {"items": [{"id": f"0190b000-0000-7000-0000-{i:012d}"} for i in range(5)]}
        mock_httpx_client.get.return_value = _make_response(json_data=page_data)

        client = WIPClient(config)
        result = client.fetch_all_paginated("def-store", "/terminologies", page_size=100)

        assert len(result) == 5
        # Should only make one GET call
        assert mock_httpx_client.get.call_count == 1

    def test_multiple_pages(self, config, mock_httpx_client):
        """When results span multiple pages, all pages are fetched."""
        page1 = {"items": [{"id": f"0190b000-0000-7000-0000-{i:012d}"} for i in range(3)]}
        page2 = {"items": [{"id": f"0190b000-0000-7000-0000-{i:012d}"} for i in range(3, 5)]}

        mock_httpx_client.get.side_effect = [
            _make_response(json_data=page1),
            _make_response(json_data=page2),
        ]

        client = WIPClient(config)
        result = client.fetch_all_paginated("def-store", "/terminologies", page_size=3)

        assert len(result) == 5
        assert mock_httpx_client.get.call_count == 2

    def test_empty_result(self, config, mock_httpx_client):
        """Empty endpoint returns empty list."""
        mock_httpx_client.get.return_value = _make_response(json_data={"items": []})

        client = WIPClient(config)
        result = client.fetch_all_paginated("def-store", "/terminologies")

        assert result == []

    def test_page_size_param_passed(self, config, mock_httpx_client):
        """page_size and page params are passed correctly."""
        mock_httpx_client.get.return_value = _make_response(json_data={"items": []})

        client = WIPClient(config)
        client.fetch_all_paginated("def-store", "/terminologies", page_size=50)

        call_args = mock_httpx_client.get.call_args
        params = call_args[1]["params"]
        assert params["page_size"] == 50
        assert params["page"] == 1

    def test_custom_items_key(self, config, mock_httpx_client):
        """Custom items_key is used for extraction."""
        mock_httpx_client.get.return_value = _make_response(
            json_data={"results": [{"id": "1"}, {"id": "2"}]},
        )

        client = WIPClient(config)
        result = client.fetch_all_paginated(
            "registry", "/entries", items_key="results", page_size=100,
        )

        assert len(result) == 2

    def test_preserves_existing_params(self, config, mock_httpx_client):
        """Extra params are merged with pagination params."""
        mock_httpx_client.get.return_value = _make_response(json_data={"items": []})

        client = WIPClient(config)
        client.fetch_all_paginated(
            "def-store", "/terminologies",
            params={"namespace": "wip", "status": "active"},
        )

        call_args = mock_httpx_client.get.call_args
        params = call_args[1]["params"]
        assert params["namespace"] == "wip"
        assert params["status"] == "active"
        assert "page_size" in params
        assert "page" in params

    def test_three_pages_with_exact_boundary(self, config, mock_httpx_client):
        """Full pages followed by a partial page signals end of pagination."""
        page1 = {"items": [{"id": f"0190b000-0000-7000-0000-{i:012d}"} for i in range(2)]}
        page2 = {"items": [{"id": f"0190b000-0000-7000-0000-{i:012d}"} for i in range(2, 4)]}
        page3 = {"items": [{"id": "0190b000-0000-7000-0000-000000000004"}]}

        mock_httpx_client.get.side_effect = [
            _make_response(json_data=page1),
            _make_response(json_data=page2),
            _make_response(json_data=page3),
        ]

        client = WIPClient(config)
        result = client.fetch_all_paginated("def-store", "/terminologies", page_size=2)

        assert len(result) == 5
        assert mock_httpx_client.get.call_count == 3


class TestCheckHealth:
    """Test service health checks."""

    def test_healthy_service(self, config, mock_httpx_client):
        mock_httpx_client.get.return_value = _make_response(status_code=200)

        client = WIPClient(config)
        healthy, msg = client.check_health("registry")

        assert healthy is True
        assert msg == "healthy"

    def test_unhealthy_service(self, config, mock_httpx_client):
        mock_httpx_client.get.return_value = _make_response(status_code=503)

        client = WIPClient(config)
        healthy, msg = client.check_health("registry")

        assert healthy is False
        assert "503" in msg

    def test_connection_refused(self, config, mock_httpx_client):
        mock_httpx_client.get.side_effect = httpx.ConnectError("Connection refused")

        client = WIPClient(config)
        healthy, msg = client.check_health("registry")

        assert healthy is False
        assert msg == "connection refused"

    def test_timeout(self, config, mock_httpx_client):
        mock_httpx_client.get.side_effect = httpx.TimeoutException("Timeout")

        client = WIPClient(config)
        healthy, msg = client.check_health("registry")

        assert healthy is False
        assert msg == "timeout"

    def test_unexpected_exception(self, config, mock_httpx_client):
        mock_httpx_client.get.side_effect = RuntimeError("Something broke")

        client = WIPClient(config)
        healthy, msg = client.check_health("registry")

        assert healthy is False
        assert "Something broke" in msg

    def test_health_uses_root_endpoint(self, config, mock_httpx_client):
        """Health check goes to /health on the base URL, not the API prefix."""
        mock_httpx_client.get.return_value = _make_response(status_code=200)

        client = WIPClient(config)
        client.check_health("registry")

        call_args = mock_httpx_client.get.call_args
        url = call_args[0][0]
        # Should be http://localhost:8001/health, NOT /api/registry/health
        assert url == "http://localhost:8001/health"


class TestCheckAllServices:
    """Test check_all_services."""

    def test_all_healthy(self, config, mock_httpx_client):
        mock_httpx_client.get.return_value = _make_response(status_code=200)

        client = WIPClient(config)
        results = client.check_all_services()

        assert len(results) == 4
        for service, (healthy, msg) in results.items():
            assert healthy is True

    def test_mixed_health(self, config, mock_httpx_client):
        """Some services healthy, some not."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_response(status_code=200)
            else:
                raise httpx.ConnectError("Connection refused")

        mock_httpx_client.get.side_effect = side_effect

        client = WIPClient(config)
        results = client.check_all_services()

        healthy_count = sum(1 for _, (h, _) in results.items() if h)
        unhealthy_count = sum(1 for _, (h, _) in results.items() if not h)
        assert healthy_count == 2
        assert unhealthy_count == 2


class TestWIPClientContextManager:
    """Test context manager protocol."""

    def test_enter_returns_self(self, config, mock_httpx_client):
        client = WIPClient(config)
        result = client.__enter__()
        assert result is client

    def test_exit_closes_client(self, config, mock_httpx_client):
        client = WIPClient(config)
        client.__exit__(None, None, None)
        mock_httpx_client.close.assert_called_once()

    def test_with_statement(self, config, mock_httpx_client):
        with WIPClient(config) as client:
            assert isinstance(client, WIPClient)
        mock_httpx_client.close.assert_called_once()


class TestWIPClientVerbose:
    """Test verbose logging does not break requests."""

    def test_verbose_get(self, mock_httpx_client):
        config = WIPConfig(
            host="localhost", proxy=False, api_key="key", verbose=True,
        )
        mock_httpx_client.get.return_value = _make_response(json_data={"ok": True})

        client = WIPClient(config)
        result = client.get("registry", "/namespaces")
        assert result == {"ok": True}

    def test_verbose_post(self, mock_httpx_client):
        config = WIPConfig(
            host="localhost", proxy=False, api_key="key", verbose=True,
        )
        mock_httpx_client.post.return_value = _make_response(json_data={"ok": True})

        client = WIPClient(config)
        result = client.post("registry", "/entries/register", json=[])
        assert result == {"ok": True}


class TestWIPClientError:
    """Test WIPClientError exception class."""

    def test_message(self):
        err = WIPClientError("something went wrong")
        assert str(err) == "something went wrong"

    def test_status_code(self):
        err = WIPClientError("not found", status_code=404)
        assert err.status_code == 404

    def test_response_body(self):
        body = {"detail": "not found"}
        err = WIPClientError("not found", status_code=404, response_body=body)
        assert err.response_body == body

    def test_default_status_code_none(self):
        err = WIPClientError("error")
        assert err.status_code is None

    def test_default_response_body_none(self):
        err = WIPClientError("error")
        assert err.response_body is None
