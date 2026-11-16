"""Def-Store's 409 for a terminology identifier that names several
terminologies is an answer about the reference, not an outage.

Def-Store answers 409 (error_code ambiguous_reference, the candidates in its
message) when a bare value resolves to several terminologies, for instance
one that two terminologies hold as synonyms under different qualifiers. The
client read every status but 200 and 404 as "Def-Store unavailable", so a
document naming such a terminology was refused as a dependency outage. It is
now a validation error that names the candidates.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from document_store.services.def_store_client import (
    DefStoreAmbiguousError,
    DefStoreClient,
)
from document_store.services.validation_service import ValidationResult, ValidationService

MESSAGE = (
    "terminology identifier 'SHADE' is ambiguous: it resolves to 2 entities "
    "(T1 via {'vendor_a': 'SHADE'}, T2 via {'vendor_b': 'SHADE'})"
)


def _ambiguous(request: httpx.Request) -> httpx.Response:
    return httpx.Response(409, json={"detail": {
        "error": "ambiguous", "error_code": "ambiguous_reference", "message": MESSAGE,
    }})


@pytest.mark.asyncio
async def test_the_client_reads_a_409_as_ambiguous_not_unavailable():
    client = DefStoreClient(base_url="http://def-store", api_key="k")
    with patch("document_store.services.def_store_client.get_def_store_transport",
               return_value=httpx.MockTransport(_ambiguous)), pytest.raises(DefStoreAmbiguousError) as caught:
        await client.get_terminology("SHADE", "appns")
    assert caught.value.unavailable is False
    assert str(caught.value) == MESSAGE


@pytest.mark.asyncio
async def test_a_document_naming_an_ambiguous_terminology_gets_a_validation_error():
    client = DefStoreClient(base_url="http://def-store", api_key="k")
    result = ValidationResult()
    with patch("document_store.services.def_store_client.get_def_store_transport",
               return_value=httpx.MockTransport(_ambiguous)), \
         patch("document_store.services.validation_service.get_def_store_client",
               return_value=client):
        resolved = await ValidationService()._resolve_terminology_reference(
            "SHADE", result, "vocab", namespace="appns",
        )
    assert resolved is None
    assert [(e["code"], e["field"]) for e in result.errors] == [("ambiguous_reference", "vocab")]
    assert "T1" in result.errors[0]["message"] and "T2" in result.errors[0]["message"]
