"""CASE-589: pin the qualified NS:VALUE cross-namespace reference form.

CASE-540 established (and live-verified) that cross-namespace by-value
references already work via the explicit qualifier parsed in
wip_auth/resolve.py — bare VALUE resolves in the caller's own namespace,
NS:VALUE crosses explicitly (the namespace rides inside the hashed
composite key: one deterministic lookup, no fallback search). These are
the first committed regression tests of that form at the template-ref
sites — the untested-load-bearing-path risk this case exists to close.

Runs against the REAL registry mounted in-process (conftest ASGI wiring),
so the full normalize -> Registry resolve -> canonical-ID storage path is
exercised, not a mock.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app
from registry.models.namespace import Namespace

API = "/api/template-store"


async def _mk_namespace(prefix: str, allowed_external_refs: list[str] | None = None):
    await Namespace(
        prefix=prefix,
        description=f"CASE-589 test namespace: {prefix}",
        allowed_external_refs=allowed_external_refs or [],
    ).insert()


def _template(namespace: str, value: str, extra_fields: list[dict] | None = None) -> dict:
    return {
        "namespace": namespace,
        "value": value,
        "label": value,
        "description": f"CASE-589 {value}",
        "fields": [
            {"name": "name", "label": "Name", "type": "string", "mandatory": True},
            *(extra_fields or []),
        ],
        "identity_fields": ["name"],
    }


async def _post_template(client: AsyncClient, auth_headers: dict, body: dict) -> dict:
    resp = await client.post(f"{API}/templates", headers=auth_headers, json=[body])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _get_template_fields(
    client: AsyncClient, auth_headers: dict, value: str, namespace: str
) -> list[dict]:
    resp = await client.get(
        f"{API}/templates/by-value/{value}",
        headers=auth_headers,
        params={"namespace": namespace},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["fields"]


@pytest.mark.asyncio
async def test_qualified_template_ref_resolves_cross_namespace(
    client: AsyncClient, auth_headers: dict
):
    """The CASE-518/540 shape: target_templates by qualified VALUE, not UUID."""
    await _mk_namespace("ns589-tgt")
    await _mk_namespace("ns589-src", allowed_external_refs=["ns589-tgt"])

    tgt = await _post_template(client, auth_headers, _template("ns589-tgt", "TGT_589"))
    assert tgt["status"] == "created", tgt

    src = await _post_template(
        client, auth_headers,
        _template("ns589-src", "SRC_589", extra_fields=[{
            "name": "tgt",
            "label": "Target",
            "type": "reference",
            "reference_type": "document",
            "target_templates": ["ns589-tgt:TGT_589"],
        }]),
    )
    assert src["status"] == "created", src

    # The stored template must carry the foreign CANONICAL id — the
    # qualified value normalized through the real Registry.
    fields = await _get_template_fields(client, auth_headers, "SRC_589", "ns589-src")
    tgt_field = next(f for f in fields if f["name"] == "tgt")
    assert tgt_field["target_templates"] == [tgt["id"]]


@pytest.mark.asyncio
async def test_bare_value_stays_own_namespace(client: AsyncClient, auth_headers: dict):
    """A bare foreign value must NOT resolve across the boundary — bare means
    own-namespace, deterministically (the anti-guessing-game rule)."""
    await _mk_namespace("ns589-tgt")
    await _mk_namespace("ns589-src", allowed_external_refs=["ns589-tgt"])
    tgt = await _post_template(client, auth_headers, _template("ns589-tgt", "TGT_589"))
    assert tgt["status"] == "created", tgt

    bare = await _post_template(
        client, auth_headers,
        _template("ns589-src", "SRC_BARE_589", extra_fields=[{
            "name": "tgt",
            "label": "Target",
            "type": "reference",
            "reference_type": "document",
            "target_templates": ["TGT_589"],
        }]),
    )
    assert bare["status"] == "error", bare
    assert "TGT_589" in (bare.get("error") or "")


@pytest.mark.asyncio
async def test_qualified_terminology_ref_resolves_cross_namespace(
    client: AsyncClient, auth_headers: dict
):
    """The terminology analogue — same normalize site, same qualifier form."""
    await _mk_namespace("ns589-tgt")
    await _mk_namespace("ns589-src", allowed_external_refs=["ns589-tgt"])

    # Register a terminology entity + its {ns,type,value} auto-synonym in the
    # foreign namespace, the same way def-store does (conftest pattern).
    headers = {
        "X-API-Key": os.environ["MASTER_API_KEY"],
        "Content-Type": "application/json",
    }
    transport = ASGITransport(app=registry_app)
    async with AsyncClient(transport=transport, base_url="http://registry") as reg:
        resp = await reg.post(
            "/api/registry/entries/register",
            headers=headers,
            json=[{
                "namespace": "ns589-tgt",
                "entity_type": "terminologies",
                "composite_key": {"value": "GENDER_589", "label": "GENDER_589"},
            }],
        )
        entry_id = resp.json()["results"][0]["registry_id"]
        await reg.post(
            "/api/registry/synonyms/add",
            headers=headers,
            json=[{
                "target_id": entry_id,
                "synonym_namespace": "ns589-tgt",
                "synonym_entity_type": "terminologies",
                "synonym_composite_key": {
                    "ns": "ns589-tgt",
                    "type": "terminology",
                    "value": "GENDER_589",
                },
            }],
        )

    src = await _post_template(
        client, auth_headers,
        _template("ns589-src", "SRC_TERM_589", extra_fields=[{
            "name": "gender",
            "label": "Gender",
            "type": "term",
            "terminology_ref": "ns589-tgt:GENDER_589",
        }]),
    )
    assert src["status"] == "created", src

    fields = await _get_template_fields(client, auth_headers, "SRC_TERM_589", "ns589-src")
    term_field = next(f for f in fields if f["name"] == "gender")
    assert term_field["terminology_ref"] == entry_id
