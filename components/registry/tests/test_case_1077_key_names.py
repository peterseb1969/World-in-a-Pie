"""No API key may be named "apikey:<name>" (CASE-1077).

A key presents its user_id as "apikey:<name>". For callers on an older
wip-auth, permission checks, reservation ownership and grant writes read that
spelling as the bare name, so a key named "apikey:ci" acted as the key "ci":
it held ci's grants and reservations, and every grant made to it was stored
under ci. Every way a key enters the Registry now refuses the name: the API,
the install's key file, and a system restore.
"""

import pytest

from registry.services import key_import

from . import test_case_1071_scope_holes as scope_tests
from .test_case_1033_key_import import _key_file, _p
from .test_case_1071_scope_holes import _backup, _grant, _key, _restore, _two_namespaces

fresh = scope_tests.fresh  # the shared fixture

KEYS = "/api/registry/api-keys"


@pytest.mark.asyncio
async def test_the_api_refuses_a_name_spelled_like_another_key(fresh, auth_headers):
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    ci = _p("ci")
    await _key(client, auth_headers, ci, [a])
    await _grant(client, auth_headers, a, ci)

    r = await client.post(KEYS, headers=auth_headers, json={"name": f"apikey:{ci}", "namespaces": [a]})
    assert r.status_code == 422, r.text
    assert "apikey:" in r.text


@pytest.mark.asyncio
async def test_the_key_file_with_such_a_name_is_not_imported(client, monkeypatch, tmp_path):
    name = f"apikey:{_p('app')}"
    _key_file(monkeypatch, tmp_path, {"name": name, "key": "pt-x", "owner": "app:x",
                                      "groups": [], "namespaces": ["default"]})
    report = await key_import.run_pass()
    assert report.skipped and "apikey:" in report.skipped[0]
    assert name not in report.keys_created


@pytest.mark.asyncio
async def test_a_restore_skips_an_archived_key_with_such_a_name(fresh, auth_headers):
    client = fresh
    a, _ = await _two_namespaces(client, auth_headers)
    k = _p("k")
    await _key(client, auth_headers, k, [a])
    archive = await _backup(client, auth_headers)
    bad = f"apikey:{_p('ci')}"
    archive["api_keys"].append(
        {**next(x for x in archive["api_keys"] if x["name"] == k), "name": bad}
    )

    report = await _restore(client, auth_headers, archive, dry_run=False)
    skipped = {x["name"]: x["reason_code"] for x in report["api_keys"]["skipped"]}
    assert skipped[bad] == "invalid_record"
    assert bad not in report["api_keys"]["created"]
    listed = (await client.get(f"{KEYS}?page_size=1000", headers=auth_headers)).json()["items"]
    assert bad not in {x["name"] for x in listed}
