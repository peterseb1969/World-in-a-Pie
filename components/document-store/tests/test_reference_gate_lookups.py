"""The outbound reference gate asks the Registry only when the answer matters.

Only a strict namespace's isolation_mode can refuse a reference, so for any
other source the gate returns before looking up where the targets live: one
Registry lookup per written document used to dominate bulk loads of
term-bearing documents and decided nothing. A strict namespace's batch places
all of its references with one lookup per 500 ids. Stage 1b of a bulk write
is reported in the response's timing as 1b_reference_gates.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from document_store.services.reference_validator import (
    PLACEMENT_CHUNK,
    ReferenceValidationError,
    ReferenceValidator,
)
from registry.main import app as registry_app
from wip_auth.registry_client import RegistryClientBase

DOCS = "/api/document-store/documents"
STRICT = "gate-strict"


class _FakeRegistry:
    """Answers namespace settings and placements; records every placement."""

    def __init__(self, config: dict, placed: dict[str, str] | None = None):
        self.config = config
        self.placed = placed or {}
        self.lookups: list[list[str]] = []

    async def get_namespace(self, namespace):
        return self.config

    async def lookup_entry_namespaces(self, entry_ids, raise_on_error=False):
        self.lookups.append(list(entry_ids))
        return {eid: self.placed[eid] for eid in entry_ids if eid in self.placed}


def _validator(registry: _FakeRegistry) -> ReferenceValidator:
    v = ReferenceValidator()
    v._registry = registry  # type: ignore[assignment]
    return v


def _terms(*ids: str) -> list[dict]:
    return [{"term_id": t, "field_path": "f"} for t in ids]


# --- the validator ----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_open_source_places_nothing():
    reg = _FakeRegistry({"isolation_mode": "open"}, {"t1": "elsewhere"})
    await _validator(reg).validate_document_references(
        document_namespace="appns", template_namespace="wip",
        term_references=_terms("t1", "t2"),
    )
    assert reg.lookups == []


@pytest.mark.asyncio
async def test_an_open_source_batch_places_nothing():
    reg = _FakeRegistry({"isolation_mode": "open"})
    placed = await _validator(reg).place_references(
        "appns", [{"term_references": _terms("t1")}],
    )
    assert placed == {}
    assert reg.lookups == []


@pytest.mark.asyncio
async def test_a_strict_source_still_refuses_a_foreign_term():
    reg = _FakeRegistry(
        {"isolation_mode": "strict", "allowed_external_refs": ["wip"]},
        {"t1": "other"},
    )
    with pytest.raises(ReferenceValidationError):
        await _validator(reg).validate_document_references(
            document_namespace="appns", template_namespace="appns",
            term_references=_terms("t1"),
        )
    assert reg.lookups == [["t1"]]


@pytest.mark.asyncio
async def test_a_strict_source_accepts_an_allowed_namespace():
    reg = _FakeRegistry(
        {"isolation_mode": "strict", "allowed_external_refs": ["wip"]},
        {"t1": "wip"},
    )
    await _validator(reg).validate_document_references(
        document_namespace="appns", template_namespace="appns",
        term_references=_terms("t1"),
    )


@pytest.mark.asyncio
async def test_a_strict_batch_is_placed_in_chunks_and_never_asked_again():
    ids = [f"t{i:04d}" for i in range(PLACEMENT_CHUNK * 2 + 1)]
    reg = _FakeRegistry(
        {"isolation_mode": "strict", "allowed_external_refs": []},
        dict.fromkeys(ids[:-1], "appns"),  # the last id is not an active entity
    )
    v = _validator(reg)
    docs = [{"term_references": _terms(t)} for t in ids]
    placed = await v.place_references("appns", docs)
    assert [len(chunk) for chunk in reg.lookups] == [PLACEMENT_CHUNK, PLACEMENT_CHUNK, 1]
    assert placed[ids[-1]] == ""

    for doc in docs[:-1]:
        await v.validate_document_references(
            document_namespace="appns", template_namespace="appns",
            placements=placed, **doc,
        )
    # The id the Registry could not place is refused, without asking again.
    with pytest.raises(ReferenceValidationError) as refused:
        await v.validate_document_references(
            document_namespace="appns", template_namespace="appns",
            placements=placed, **docs[-1],
        )
    assert refused.value.violations[0]["entity_id"] == ids[-1]
    assert len(reg.lookups) == 3


@pytest.mark.asyncio
async def test_a_reference_the_previous_version_held_is_not_checked():
    reg = _FakeRegistry({"isolation_mode": "strict", "allowed_external_refs": []}, {"t1": "other"})
    await _validator(reg).validate_document_references(
        document_namespace="appns", template_namespace="appns",
        term_references=_terms("t1"),
        previous={"term_references": _terms("t1")},
    )
    assert reg.lookups == []


# --- the write paths --------------------------------------------------------


@pytest.fixture
def placements_seen(monkeypatch) -> list[list[str]]:
    """Every Registry placement document-store sends, by the ids it asked."""
    seen: list[list[str]] = []
    original = RegistryClientBase.lookup_entry_namespaces

    async def recording(self, entry_ids, raise_on_error=False):
        seen.append(list(entry_ids))
        return await original(self, entry_ids, raise_on_error=raise_on_error)

    monkeypatch.setattr(RegistryClientBase, "lookup_entry_namespaces", recording)
    return seen


def _mock_term_id(value: str) -> str:
    """The term id the conftest's mocked def-store resolves `value` to."""
    import hashlib

    return f"0190b000-0000-7000-8000-{hashlib.sha256(value.lower().encode()).hexdigest()[:12]}"


def _person(namespace: str = "wip") -> dict:
    return {
        "template_id": "PERSON", "namespace": namespace,
        "data": {
            "national_id": f"{uuid.uuid4().int % 10**9:09d}",
            "first_name": "A", "last_name": "B", "gender": "M",
        },
    }


@pytest.mark.asyncio
async def test_an_open_namespace_batch_writes_without_placing(
    client: AsyncClient, auth_headers: dict, placements_seen,
):
    resp = await client.post(DOCS, headers=auth_headers, json=[_person(), _person(), _person()])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["created"] * 3, body
    assert placements_seen == []
    assert "1b_reference_gates" in body["timing"]


@pytest.mark.asyncio
async def test_an_open_namespace_single_write_and_patch_place_nothing(
    client: AsyncClient, auth_headers: dict, placements_seen,
):
    resp = await client.post(DOCS, headers=auth_headers, json=[_person()])
    [created] = resp.json()["results"]
    assert created["status"] == "created", created
    resp = await client.patch(DOCS, headers=auth_headers, json=[
        {"document_id": created["document_id"], "patch": {"gender": "F"}},
    ])
    [patched] = resp.json()["results"]
    assert patched["status"] == "updated", patched
    assert placements_seen == []


@pytest.mark.asyncio
async def test_a_strict_namespace_batch_places_once(
    client: AsyncClient, auth_headers: dict, placements_seen,
):
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        resp = await reg.put(f"/api/registry/namespaces/{STRICT}", headers=auth_headers, json={
            "description": "reference gate tests", "isolation_mode": "strict",
            "allowed_external_refs": ["wip"],
        })
        assert resp.status_code == 200, resp.text
        # The gender term the mocked def-store resolves "M" to lives in wip,
        # as a real term does: the strict namespace's allow-list admits it.
        # Unregistered, the Registry could not place it and the gate refuses.
        term = await reg.post("/api/registry/entries/register", headers=auth_headers, json=[{
            "namespace": "wip", "entity_type": "terms", "entry_id": _mock_term_id("M"),
            "composite_key": {"terminology_id": "TERM-GENDER", "value": "M"},
        }])
        assert term.json()["results"][0]["status"] in ("created", "already_exists"), term.text

    resp = await client.post(DOCS, headers=auth_headers, json=[_person(STRICT) for _ in range(3)])
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["created"] * 3, body
    assert len(placements_seen) == 1, placements_seen
