"""A service that cannot answer is an outage, never an answer.

- DependencyUnavailableError is the platform's one class for "could not be
  asked"; RegistryUnavailableError is the Registry's case of it, so one
  except catches every dependency outage.
- setup_auth's handlers answer 503: registry_unavailable for the Registry,
  dependency_unavailable (naming the dependency) for any other.
- A Registry lookup that answers 200 but reports an error for its item has
  not answered for that value: it raises, it is never "no match". A
  not_found item is still a miss (the Registry's answer, which also covers
  a namespace the caller cannot read).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wip_auth import (
    DependencyUnavailableError,
    RegistryUnavailableError,
    setup_auth,
)
from wip_auth.config import AuthConfig
from wip_auth.registry_client import RegistryClientBase

REGISTRY = "http://registry.test"
CANDIDATES_URL = f"{REGISTRY}/api/registry/entries/lookup/candidates"


def test_a_registry_outage_is_a_dependency_outage():
    err = RegistryUnavailableError("Registry unavailable (x): HTTP 503", "x")
    assert isinstance(err, DependencyUnavailableError)
    assert err.dependency == "registry"
    assert err.operation == "x"


def _app() -> FastAPI:
    app = FastAPI()
    setup_auth(app, AuthConfig(mode="none"))

    @app.get("/def")
    async def def_store_down():
        raise DependencyUnavailableError("def-store unavailable (term): HTTP 503", "def-store")

    @app.get("/registry")
    async def registry_down():
        raise RegistryUnavailableError("Registry unavailable (lookup): HTTP 503", "lookup")

    return app


def test_setup_auth_answers_a_dependency_outage_with_503_naming_it():
    response = TestClient(_app()).get("/def")
    assert response.status_code == 503
    body = response.json()
    assert body["error_code"] == "dependency_unavailable"
    assert body["dependency"] == "def-store"


def test_a_registry_outage_keeps_its_own_code():
    response = TestClient(_app()).get("/registry")
    assert response.status_code == 503
    assert response.json()["error_code"] == "registry_unavailable"


def _client() -> RegistryClientBase:
    return RegistryClientBase(base_url=REGISTRY, api_key="k")


@pytest.mark.asyncio
async def test_a_per_item_error_on_the_candidate_lookup_is_an_outage(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=CANDIDATES_URL,
        json={"results": [{"status": "error", "error": "lookup failed"}]},
    )
    with pytest.raises(RegistryUnavailableError, match="lookup failed"):
        await _client().resolve_candidates("ns", "documents", "X")


@pytest.mark.asyncio
async def test_a_not_found_item_is_still_a_miss(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=CANDIDATES_URL, json={"results": [{"status": "not_found"}]},
    )
    assert await _client().resolve_candidates("ns", "documents", "X") == ([], 0)


@pytest.mark.asyncio
async def test_an_ambiguous_value_still_raises_ambiguity(httpx_mock):
    from wip_auth.registry_client import AmbiguousIdentifierError

    httpx_mock.add_response(method="POST", url=CANDIDATES_URL, json={"results": [{
        "status": "found", "total": 2,
        "candidates": [{"entry_id": "a"}, {"entry_id": "b"}],
    }]})
    with pytest.raises(AmbiguousIdentifierError):
        await _client().resolve_identifier("ns", "templates", "X")
