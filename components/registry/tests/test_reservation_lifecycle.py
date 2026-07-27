"""The provision → activate lifecycle, exercised as a consumer would use it.

These three endpoints have shape tests already: provision returns ids, reserve
validates formats, activate flips a status. What was never tested is the
property a consumer would *depend* on — that a reserved entry is invisible to
resolution until it is activated. That is the whole reason the lifecycle
exists: an ID-re-minting restore provisions every entity up front, and if the
job dies halfway the namespace must be invisible and reconcilable rather than
half-resolvable.

No production code calls these endpoints yet. The re-minting restore will be
their first caller, which makes this the never-tested-default trap: a default
nobody has exercised, about to become load-bearing. Hence this file, before
that mode is built rather than after it breaks.
"""

import pytest
from httpx import AsyncClient

NAMESPACE = "default"


async def _provision(
    client: AsyncClient,
    auth_headers: dict,
    *,
    count: int = 1,
    entity_type: str = "terms",
    composite_keys: list[dict] | None = None,
    defer_claim: bool = False,
) -> list[str]:
    payload: dict = {
        "namespace": NAMESPACE,
        "entity_type": entity_type,
        "count": count,
    }
    if composite_keys is not None:
        payload["composite_keys"] = composite_keys
    if defer_claim:
        payload["defer_claim"] = True
    resp = await client.post(
        "/api/registry/entries/provision", json=payload, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    return [item["entry_id"] for item in resp.json()["ids"]]


async def _activate(
    client: AsyncClient, auth_headers: dict, entry_ids: list[str],
    composite_keys: list[dict | None] | None = None,
) -> dict:
    items: list[dict] = []
    for i, entry_id in enumerate(entry_ids):
        item: dict = {"entry_id": entry_id}
        if composite_keys is not None and composite_keys[i] is not None:
            item["composite_key"] = composite_keys[i]
        items.append(item)
    resp = await client.post(
        "/api/registry/entries/activate", json=items, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _lookup_by_id(
    client: AsyncClient, auth_headers: dict, entry_id: str
) -> dict:
    resp = await client.post(
        "/api/registry/entries/lookup/by-id",
        json=[{"entry_id": entry_id}],
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _lookup_by_key(
    client: AsyncClient, auth_headers: dict, composite_key: dict,
    entity_type: str = "terms",
) -> dict:
    resp = await client.post(
        "/api/registry/entries/lookup/by-key",
        json=[{
            "namespace": NAMESPACE,
            "entity_type": entity_type,
            "composite_key": composite_key,
        }],
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


class TestReservedEntriesDoNotResolve:
    """The property the whole lifecycle exists to provide."""

    @pytest.mark.asyncio
    async def test_a_provisioned_id_does_not_resolve(
        self, client: AsyncClient, auth_headers: dict
    ):
        (entry_id,) = await _provision(client, auth_headers)

        result = await _lookup_by_id(client, auth_headers, entry_id)

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_a_provisioned_composite_key_does_not_resolve(
        self, client: AsyncClient, auth_headers: dict
    ):
        key = {"ns": NAMESPACE, "value": "RESERVED-ONLY"}
        await _provision(client, auth_headers, composite_keys=[key])

        result = await _lookup_by_key(client, auth_headers, key)

        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_activation_makes_it_resolve(
        self, client: AsyncClient, auth_headers: dict
    ):
        key = {"ns": NAMESPACE, "value": "BECOMES-VISIBLE"}
        (entry_id,) = await _provision(client, auth_headers, composite_keys=[key])

        assert (await _lookup_by_id(client, auth_headers, entry_id))["status"] == (
            "not_found"
        )
        await _activate(client, auth_headers, [entry_id])

        by_id = await _lookup_by_id(client, auth_headers, entry_id)
        by_key = await _lookup_by_key(client, auth_headers, key)
        assert by_id["status"] == "found" and by_id["entry_id"] == entry_id
        assert by_key["status"] == "found" and by_key["entry_id"] == entry_id


class TestProvisionHoldsTheKey:
    """Reserved does not mean unclaimed.

    A crashed job must leave its keys held, not free for the next writer —
    otherwise the reconcile that cleans it up would be racing whoever took
    the key in the meantime.
    """

    @pytest.mark.asyncio
    async def test_a_second_provision_of_the_same_key_conflicts(
        self, client: AsyncClient, auth_headers: dict
    ):
        key = {"ns": NAMESPACE, "value": "CONTESTED"}
        await _provision(client, auth_headers, composite_keys=[key])

        resp = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": NAMESPACE,
                "entity_type": "terms",
                "count": 1,
                "composite_keys": [key],
            },
            headers=auth_headers,
        )

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_a_conflicting_batch_reserves_nothing(
        self, client: AsyncClient, auth_headers: dict
    ):
        # All-or-nothing: a batch that fails partway must not leave half its
        # ids reserved, or a retry would double-provision.
        key = {"ns": NAMESPACE, "value": "BATCH-CONTESTED"}
        await _provision(client, auth_headers, composite_keys=[key])

        resp = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": NAMESPACE,
                "entity_type": "terms",
                "count": 2,
                "composite_keys": [
                    {"ns": NAMESPACE, "value": "BATCH-FRESH"},
                    key,
                ],
            },
            headers=auth_headers,
        )

        assert resp.status_code == 409
        survivor = await _lookup_by_key(
            client, auth_headers, {"ns": NAMESPACE, "value": "BATCH-FRESH"}
        )
        assert survivor["status"] == "not_found"


class TestActivation:
    @pytest.mark.asyncio
    async def test_a_whole_batch_activates_in_one_call(
        self, client: AsyncClient, auth_headers: dict
    ):
        # The re-minting restore activates a namespace's worth of entries at
        # once; that is what makes it become resolvable in one step rather
        # than entity by entity.
        entry_ids = await _provision(client, auth_headers, count=5)

        result = await _activate(client, auth_headers, entry_ids)

        assert result["activated"] == 5 and result["errors"] == 0
        for entry_id in entry_ids:
            assert (await _lookup_by_id(client, auth_headers, entry_id))[
                "status"
            ] == "found"

    @pytest.mark.asyncio
    async def test_activating_twice_is_not_an_error(
        self, client: AsyncClient, auth_headers: dict
    ):
        # A retried activation must be safe: the job may have died after
        # activating and before recording that it had.
        (entry_id,) = await _provision(client, auth_headers)
        await _activate(client, auth_headers, [entry_id])

        result = await _activate(client, auth_headers, [entry_id])

        assert result["errors"] == 0
        assert result["results"][0]["status"] == "already_active"

    @pytest.mark.asyncio
    async def test_activating_an_unknown_id_is_reported_per_item(
        self, client: AsyncClient, auth_headers: dict
    ):
        (real,) = await _provision(client, auth_headers)

        result = await _activate(client, auth_headers, [real, "no-such-entry"])

        statuses = {r["entry_id"]: r["status"] for r in result["results"]}
        assert statuses[real] == "activated"
        assert statuses["no-such-entry"] == "not_found"
        assert result["activated"] == 1


class TestReserve:
    """Client-provided IDs — the same lifecycle from the other end."""

    @pytest.mark.asyncio
    async def test_a_reserved_id_does_not_resolve_until_activated(
        self, client: AsyncClient, auth_headers: dict
    ):
        entry_id = "019f7c9d-a173-7e2a-a678-4059e816e5b1"
        resp = await client.post(
            "/api/registry/entries/reserve",
            json=[{
                "entry_id": entry_id,
                "namespace": NAMESPACE,
                "entity_type": "terms",
                "composite_key": {"ns": NAMESPACE, "value": "CLIENT-CHOSEN"},
            }],
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"][0]["status"] == "reserved"

        assert (await _lookup_by_id(client, auth_headers, entry_id))["status"] == (
            "not_found"
        )
        await _activate(client, auth_headers, [entry_id])
        assert (await _lookup_by_id(client, auth_headers, entry_id))["status"] == (
            "found"
        )


class TestBulkActivationContract:
    """The activate endpoint is bulk-shaped internally (one classify read +
    one guarded update_many) since the per-entry loop made activation ~50% of
    restore wall time. These pin the per-item contract the rewrite must keep.
    """

    @pytest.mark.asyncio
    async def test_mixed_batch_reports_every_item_with_aligned_indices(
        self, client: AsyncClient, auth_headers: dict
    ):
        # One call carrying every classification at once: fresh, already
        # active, unknown, and non-reserved-non-active. The composed response
        # must line up per item exactly as the per-entry loop's did.
        fresh, aging = await _provision(client, auth_headers, count=2)
        await _activate(client, auth_headers, [aging])

        from registry.models.entry import RegistryEntry
        retired = (await _provision(client, auth_headers, count=1))[0]
        entry = await RegistryEntry.find_one({"entry_id": retired})
        entry.status = "inactive"
        await entry.save()

        result = await _activate(
            client, auth_headers, [fresh, aging, "no-such-entry", retired]
        )

        by_index = {r["index"]: r for r in result["results"]}
        assert by_index[0]["status"] == "activated"
        assert by_index[0]["entry_id"] == fresh
        assert by_index[1]["status"] == "already_active"
        assert by_index[2]["status"] == "not_found"
        assert by_index[3]["status"] == "error"
        assert "inactive" in by_index[3]["error"]
        assert result["total"] == 4
        assert result["activated"] == 1
        assert result["errors"] == 2

    @pytest.mark.asyncio
    async def test_duplicate_ids_in_one_request_do_not_misreport(
        self, client: AsyncClient, auth_headers: dict
    ):
        # update_many matches each document once; a repeated id must not trip
        # the modified-count cross-check into a phantom concurrency error.
        (entry_id,) = await _provision(client, auth_headers)

        result = await _activate(client, auth_headers, [entry_id, entry_id])

        statuses = [r["status"] for r in result["results"]]
        assert "error" not in statuses
        assert "activated" in statuses
        assert result["errors"] == 0
        assert (await _lookup_by_id(client, auth_headers, entry_id))[
            "status"
        ] == "found"

    @pytest.mark.asyncio
    async def test_large_batch_activates_fully(
        self, client: AsyncClient, auth_headers: dict
    ):
        # The restore engine sends 500-id chunks; exercise a full-size one so
        # the $in paths run at their real shape, not toy size.
        entry_ids = await _provision(client, auth_headers, count=500)

        result = await _activate(client, auth_headers, entry_ids)

        assert result["activated"] == 500
        assert result["errors"] == 0
        assert all(r["status"] == "activated" for r in result["results"])


class TestClaimAtActivation:
    """Deferred-claim reservation: the id is minted at provision but the
    composite-key claim is committed at ACTIVATION with the final key.

    A fresh restore re-mints every id, so an id-valued-identity document's
    final identity_hash is not known until all referenced ids exist. Deferring
    the claim lets every id be reserved first (defusing circular references)
    and the correct key claimed once it can be computed — the fix for the
    stale-claim bug where a remap-restored edge stored the recomputed hash but
    the claim kept the pre-remap one (un-re-addressable → duplicates on write).
    """

    @pytest.mark.asyncio
    async def test_defer_claim_does_not_claim_at_provision(
        self, client: AsyncClient, auth_headers: dict
    ):
        # The contrast to TestProvisionHoldsTheKey: with defer_claim, the same
        # key can be provisioned twice without conflict — nothing is claimed yet.
        key = {"ns": NAMESPACE, "value": "DEFERRED"}
        await _provision(
            client, auth_headers, composite_keys=[key], defer_claim=True
        )
        resp = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": NAMESPACE,
                "entity_type": "terms",
                "count": 1,
                "composite_keys": [key],
                "defer_claim": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text  # no 409 — claim deferred

    @pytest.mark.asyncio
    async def test_activation_with_the_final_key_claims_and_resolves(
        self, client: AsyncClient, auth_headers: dict
    ):
        # Reserve with a placeholder key, activate with the FINAL key — the
        # entry now resolves by that final key, and the key is now claimed.
        placeholder = {"ns": NAMESPACE, "value": "PLACEHOLDER"}
        final = {"ns": NAMESPACE, "value": "FINAL-KEY"}
        (entry_id,) = await _provision(
            client, auth_headers, composite_keys=[placeholder], defer_claim=True
        )

        result = await _activate(
            client, auth_headers, [entry_id], composite_keys=[final]
        )
        assert result["activated"] == 1 and result["errors"] == 0

        by_key = await _lookup_by_key(client, auth_headers, final)
        assert by_key["status"] == "found" and by_key["entry_id"] == entry_id
        # The placeholder was never claimed and does not resolve.
        assert (await _lookup_by_key(client, auth_headers, placeholder))[
            "status"
        ] == "not_found"
        # The final key is now claimed — a plain provision of it conflicts.
        resp = await client.post(
            "/api/registry/entries/provision",
            json={
                "namespace": NAMESPACE, "entity_type": "terms",
                "count": 1, "composite_keys": [final],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text

    @pytest.mark.asyncio
    async def test_activation_with_a_colliding_key_fails_loud(
        self, client: AsyncClient, auth_headers: dict
    ):
        # Two deferred reservations, activated with the SAME final key: the
        # first claims it, the second fails loudly rather than duplicating.
        key = {"ns": NAMESPACE, "value": "COLLIDES-AT-ACTIVATION"}
        (id_a,) = await _provision(client, auth_headers, defer_claim=True)
        (id_b,) = await _provision(client, auth_headers, defer_claim=True)

        first = await _activate(
            client, auth_headers, [id_a], composite_keys=[key]
        )
        assert first["activated"] == 1 and first["errors"] == 0

        second = await _activate(
            client, auth_headers, [id_b], composite_keys=[key]
        )
        assert second["activated"] == 0 and second["errors"] == 1
        assert "already registered" in second["results"][0]["error"].lower()
        # The loser is still reserved (not activated), not a duplicate.
        assert (await _lookup_by_id(client, auth_headers, id_b))[
            "status"
        ] == "not_found"
