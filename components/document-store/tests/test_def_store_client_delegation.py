"""document-store's DefStoreClient resolves term values through the shared
entry point (wip_auth.term_resolution) — it holds no copy of any vocabulary.

The resolution contract itself (namespace-scoped terminology refs, batch
sharing, answer shaping) is tested where it lives, in wip-auth's
test_term_resolution.py; the real end-to-end path is
test_term_resolution_contract.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

from document_store.services.def_store_client import DefStoreClient, DefStoreError
from wip_auth.term_resolution import TermResolutionError

ITEMS = [{"terminology_ref": "STATUS", "value": "open"}]


@pytest.mark.asyncio
async def test_validation_delegates_with_this_clients_endpoint_and_key():
    client = DefStoreClient(base_url="http://def-store.example", api_key="k", timeout=3.0)
    answer = [{"valid": True, "matched_term": {"term_id": "T", "value": "open"}, "matched_via": "value"}]
    with patch("document_store.services.def_store_client.resolve_term_values",
               new_callable=AsyncMock, return_value=answer) as resolve:
        assert await client.validate_values_bulk(ITEMS, namespace="ns-a") == answer
        assert (await client.validate_value("STATUS", "open", namespace="ns-a")) == answer[0]
    resolve.assert_any_await(ITEMS, "ns-a", base_url="http://def-store.example", api_key="k", timeout=3.0)


@pytest.mark.asyncio
async def test_resolution_failure_surfaces_as_def_store_error():
    client = DefStoreClient(base_url="http://def-store.example", api_key="k")
    with patch("document_store.services.def_store_client.resolve_term_values",
               new_callable=AsyncMock, side_effect=TermResolutionError("down")), \
         pytest.raises(DefStoreError, match="down"):
        await client.validate_values_bulk(ITEMS, namespace="ns-a")
