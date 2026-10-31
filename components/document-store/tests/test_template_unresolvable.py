"""A write against a template whose inheritance chain is broken fails with
its own code.

Template Store answers a resolving read of such a template with 409
``inheritance_unresolved``. Document-store must not validate against the
child's own fields alone (that accepts data the full schema refuses), and the
caller must be able to tell a broken chain from an unreachable Template Store.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from document_store.services.template_store_client import (
    TemplateStoreClient,
    TemplateStoreError,
)
from document_store.services.validation_service import ValidationResult, ValidationService

DETAIL = "Template 'CHILD' v1 cannot be resolved: Parent template 'p' not found at version 7"


def _http_returning(response: httpx.Response) -> MagicMock:
    http = MagicMock()
    http.get = AsyncMock(return_value=response)
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    return http


@pytest.mark.asyncio
async def test_the_client_carries_template_stores_error_code():
    response = httpx.Response(409, json={
        "detail": DETAIL, "error_code": "inheritance_unresolved",
        "details": {"rule": "missing_parent"},
    })
    client = TemplateStoreClient(base_url="http://ts", api_key="k")
    with (
        patch("httpx.AsyncClient", return_value=_http_returning(response)),
        pytest.raises(TemplateStoreError) as exc,
    ):
        await client.get_template(template_id="t-1", version=1)

    assert exc.value.code == "inheritance_unresolved"
    assert str(exc.value) == DETAIL


@pytest.mark.asyncio
async def test_a_409_without_a_json_body_still_raises():
    client = TemplateStoreClient(base_url="http://ts", api_key="k")
    with (
        patch("httpx.AsyncClient", return_value=_http_returning(httpx.Response(409, text="nope"))),
        pytest.raises(TemplateStoreError) as exc,
    ):
        await client.get_template(template_id="t-2", version=1)
    assert exc.value.code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TemplateStoreError(DETAIL, code="inheritance_unresolved"), "template_unresolvable"),
        (TemplateStoreError("Request failed: connection refused"), "template_error"),
    ],
)
async def test_the_write_fails_with_a_code_that_names_the_cause(error, code):
    client = MagicMock()
    client.get_template_resolved = AsyncMock(side_effect=error)
    result = ValidationResult()
    with patch(
        "document_store.services.validation_service.get_template_store_client",
        return_value=client,
    ):
        template = await ValidationService()._resolve_template("t-1", result, version=1)

    assert template is None
    assert result.valid is False
    assert [e["code"] for e in result.errors] == [code]
    if code == "template_unresolvable":
        assert result.errors[0]["message"] == DETAIL
