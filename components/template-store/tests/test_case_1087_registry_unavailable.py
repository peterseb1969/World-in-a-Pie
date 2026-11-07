"""A Registry that does not answer is never read as "no such template".

Provenance: CASE-1087. Resolving a template write's `extends` raised
EntityNotFoundError when the Registry was unreachable, the same as a genuine
miss, so the write fell through to the by-value lookups as if the parent were
unknown to the Registry. An outage now refuses the request (503) instead of
reading as a miss.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from template_store.services.reference_validator import (
    RegistryUnavailableError as ValidatorRegistryUnavailableError,
)
from wip_auth import RegistryUnavailableError

_PATCH_TARGET = "template_store.services.template_service.resolve_entity_id"


def test_the_service_uses_the_platform_class():
    assert ValidatorRegistryUnavailableError is RegistryUnavailableError


@pytest.mark.asyncio
async def test_extends_outage_refuses_the_write(client: AsyncClient, auth_headers: dict):
    async def unavailable(*_args, **_kwargs):
        raise RegistryUnavailableError("Registry unavailable (resolve): HTTP 500")

    with patch(_PATCH_TARGET, side_effect=unavailable):
        resp = await client.post(
            "/api/template-store/templates",
            headers=auth_headers,
            json=[{
                "namespace": "wip",
                "value": "OUTAGE_CHILD",
                "label": "child",
                "extends": "SOME_PARENT",
                "extends_version": 1,
                "identity_fields": ["k"],
                "fields": [{"name": "k", "label": "K", "type": "string", "mandatory": True}],
            }],
        )
    # Template create answers a Registry failure for the whole request (502
    # for a refusal, as before); an outage is 503 through setup_auth's handler.
    assert resp.status_code == 503, resp.text
    assert resp.json()["error_code"] == "registry_unavailable"
