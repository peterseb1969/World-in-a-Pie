"""CASE-791: system terminologies must be resolvable by value.

Listing a system terminology's terms by VALUE (`GET
/terminologies/_ONTOLOGY_RELATIONSHIP_TYPES/terms`) went through the Registry
resolver (`resolve_or_404`), which needs the terminology's value-form synonym
`{ns, type, value}`. The bootstrap used to hand-roll `register_terminology` +
a direct insert and never registered that synonym, so system terminologies
404'd by value while the UUID form and the validate path resolved fine.

The fix routes `ensure_system_terminologies` through the normal
`create_terminology` / `create_terms_bulk` service methods, which register the
value-form synonym like every other terminology. This pins that: after the real
bootstrap runs, value-form resolution works.

The conftest pre-seeds system terminologies straight into Mongo with hardcoded
`SYS-*` ids (bypassing the Registry — the very assumption this case corrects),
so the test clears that pre-seed first and runs the real bootstrap.
"""

import pytest
from httpx import AsyncClient

from def_store.models.term import Term
from def_store.models.terminology import Terminology
from def_store.services.system_terminologies import ensure_system_terminologies


@pytest.mark.asyncio
async def test_system_terminology_resolves_by_value_after_bootstrap(
    client: AsyncClient, auth_headers: dict
):
    # Drop the conftest's registry-bypassing SYS-* pre-seed so the real
    # bootstrap (through the normal create path) runs.
    await Term.find({"terminology_id": {"$regex": "^SYS-"}}).delete()
    await Terminology.find({"terminology_id": {"$regex": "^SYS-"}}).delete()

    summary = await ensure_system_terminologies()
    assert not summary["errors"], summary["errors"]
    assert summary["terminologies_created"] >= 2  # _TIME_UNITS + _ONTOLOGY_...

    # Provenance is preserved through the create path via the actor override —
    # system terminologies read as system-created, not "anonymous".
    seeded = await Terminology.find_one(
        {"namespace": "wip", "value": "_ONTOLOGY_RELATIONSHIP_TYPES"}
    )
    assert seeded is not None and seeded.created_by == "system:bootstrap"

    # The reported failure: list_terms by VALUE. It resolves through the
    # Registry now that the value-form synonym exists.
    resp = await client.get(
        "/api/def-store/terminologies/_ONTOLOGY_RELATIONSHIP_TYPES/terms",
        params={"namespace": "wip"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] >= 1

    # A second system terminology resolves too (the bug was every system
    # terminology, not just this one) — and the UUID/value forms agree.
    resp = await client.get(
        "/api/def-store/terminologies/_TIME_UNITS/terms",
        params={"namespace": "wip"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
