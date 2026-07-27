"""CASE-791: system terminologies must be resolvable by value.

Listing a system terminology's terms by VALUE (`GET
/terminologies/_ONTOLOGY_RELATIONSHIP_TYPES/terms`) goes through the Registry
resolver (`resolve_or_404`), which needs the terminology's value-form synonym
`{ns, type, value}`. The bootstrap used to hand-roll `register_terminology` +
a direct insert and never registered that synonym, so system terminologies
404'd by value while the UUID form and the validate path resolved fine. The fix
routes `ensure_system_terminologies` through the normal create path, which
registers the value-form synonym like every other terminology.

Since CASE-799 the conftest seeds system terminologies through that same real
bootstrap (create path + Registry), so this test just asserts the production
behaviour: value-form `list_terms` resolves, for every system terminology, and
provenance is `system:bootstrap`. It guards both fixes — revert either (drop the
synonym, or reseed by direct Mongo insert) and value-form resolution 404s here.
"""

import pytest
from httpx import AsyncClient

from def_store.models.terminology import Terminology


@pytest.mark.asyncio
async def test_system_terminology_resolves_by_value(
    client: AsyncClient, auth_headers: dict
):
    # The reported failure: list_terms by VALUE. Resolves through the Registry
    # now that the value-form synonym exists.
    resp = await client.get(
        "/api/def-store/terminologies/_ONTOLOGY_RELATIONSHIP_TYPES/terms",
        params={"namespace": "wip"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] >= 1

    # Every system terminology, not just this one (the bug was the seed path).
    resp = await client.get(
        "/api/def-store/terminologies/_TIME_UNITS/terms",
        params={"namespace": "wip"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Provenance preserved through the create path (actor override) — system
    # terminologies read as system-created, not "anonymous".
    seeded = await Terminology.find_one(
        {"namespace": "wip", "value": "_ONTOLOGY_RELATIONSHIP_TYPES"}
    )
    assert seeded is not None and seeded.created_by == "system:bootstrap"
