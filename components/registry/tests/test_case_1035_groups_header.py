"""The Registry reads a forwarded identity's groups as one JSON array.

Provenance: CASE-1035 (reopen). The groups used to arrive comma-joined and be
split again, so a JWT group named "staff,wip-admins" was read as wip-admins
and answered admin on every namespace. A group name is now taken whole, a
header that is not a JSON array of strings is refused, and the retired
`groups` query parameter is no longer read.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

CHECK = "/api/registry/my/check-permission"
ACCESSIBLE = "/api/registry/my/accessible-namespaces"
USER = {"user_id": "mallory-id", "email": "mallory@example.com"}


@pytest.mark.asyncio
async def test_a_comma_inside_a_group_name_is_not_a_privileged_group(
    client: AsyncClient, auth_headers: dict
):
    headers = {**auth_headers, "X-User-Groups": '["staff,wip-admins"]'}
    check = await client.get(CHECK, params={**USER, "namespace": "default"}, headers=headers)
    assert check.status_code == 200
    assert check.json()["permission"] == "none"
    listing = await client.get(ACCESSIBLE, params=USER, headers=headers)
    assert listing.status_code == 200
    assert listing.json() == {"namespaces": [], "is_superadmin": False}


@pytest.mark.asyncio
async def test_a_grant_to_a_group_whose_name_has_a_comma_matches_it_whole(
    client: AsyncClient, auth_headers: dict
):
    await client.post(
        "/api/registry/namespaces/default/grants",
        json=[{"subject": "sales,emea", "subject_type": "group", "permission": "read"}],
        headers=auth_headers,
    )
    whole = {**auth_headers, "X-User-Groups": '["sales,emea"]'}
    split = {**auth_headers, "X-User-Groups": '["sales", "emea"]'}
    params = {**USER, "namespace": "default"}
    assert (await client.get(CHECK, params=params, headers=whole)).json()["permission"] == "read"
    assert (await client.get(CHECK, params=params, headers=split)).json()["permission"] == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["wip-admins", "staff,wip-admins", '{"g": 1}', "[1]", "["])
async def test_a_header_that_is_not_an_array_of_strings_is_refused(
    client: AsyncClient, auth_headers: dict, value: str
):
    headers = {**auth_headers, "X-User-Groups": value}
    check = await client.get(CHECK, params={**USER, "namespace": "default"}, headers=headers)
    assert check.status_code == 400
    listing = await client.get(ACCESSIBLE, params=USER, headers=headers)
    assert listing.status_code == 400


@pytest.mark.asyncio
async def test_the_groups_query_parameter_is_no_longer_read(
    client: AsyncClient, auth_headers: dict
):
    params = {**USER, "namespace": "default", "groups": "wip-admins"}
    check = await client.get(CHECK, params=params, headers=auth_headers)
    assert check.status_code == 200
    assert check.json()["permission"] == "none"
