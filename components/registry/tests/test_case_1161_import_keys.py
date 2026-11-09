"""Key-file import branches the suite did not reach, pinned before
_import_keys' refactor.

Provenance: CASE-1161. A key the file gives by hash (no plaintext) is
created with that hash, left alone while the hash stays, and updated when
it changes; a key the file drops takes the API reservations it owns with
it, while a reservation the deployer manages stays.
"""

import uuid

import pytest

from registry.models.api_key import StoredAPIKey
from registry.models.reservation import RuntimeReservation
from registry.services import key_import
from tests.test_case_1033_key_import import _app_entry, _key_file, _p, _res_file
from wip_auth.providers.api_key import hash_api_key


def _hash_entry(name: str, key_hash: str) -> dict:
    return {"name": name, "key_hash": key_hash, "owner": f"app:{name}", "groups": [], "namespaces": []}


@pytest.mark.asyncio
async def test_a_key_given_by_hash_is_created_kept_and_updated(client, monkeypatch, tmp_path):
    name = _p("hash-k")
    first, second = hash_api_key("pt-one"), hash_api_key("pt-two")
    _key_file(monkeypatch, tmp_path, _hash_entry(name, first))
    report = await key_import.run_pass()
    assert name in report.keys_created
    assert (await StoredAPIKey.find_one(StoredAPIKey.name == name)).key_hash == first

    report = await key_import.run_pass()
    assert name not in report.keys_created and name not in report.keys_updated

    _key_file(monkeypatch, tmp_path, _hash_entry(name, second))
    report = await key_import.run_pass()
    assert report.keys_updated == [name]
    assert (await StoredAPIKey.find_one(StoredAPIKey.name == name)).key_hash == second


@pytest.mark.asyncio
async def test_a_dropped_key_releases_its_own_reservations_and_keeps_managed_ones(client, monkeypatch, tmp_path):
    name = _p("app-res")
    own, managed = _p("own"), _p("mgd")
    _key_file(monkeypatch, tmp_path, _app_entry(name, "pt-" + name, [], {}))
    _res_file(monkeypatch, tmp_path, {"prefix": managed, "app": name})
    await key_import.run_pass()
    await RuntimeReservation(prefix=own, namespace_id=str(uuid.uuid4()), app=name).insert()

    _key_file(monkeypatch, tmp_path)
    report = await key_import.run_pass()
    assert name in report.keys_removed
    assert await RuntimeReservation.find_one(RuntimeReservation.prefix == own) is None
    kept = await RuntimeReservation.find_one(RuntimeReservation.prefix == managed)
    assert kept is not None and kept.managed_by == "wip-deploy"
