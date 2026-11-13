"""What the last pass did with the key file is reported, not only logged.

A key file that names a key twice is refused whole: nothing in it is imported,
the previous import keeps serving and the Registry stays healthy, so every
apply reports success while no key change takes effect. The health answer says
what the last pass did with the file (never why: the reason names paths and
keys, and the endpoint is public). Only a pass that finished importing says
"imported": one that raised partway says "failed", and a Registry that has
not finished a pass says "pending", so neither can be read as an import.
"""

from __future__ import annotations

import pytest

from registry.services import key_import
from tests.test_case_1033_key_import import _app_entry, _key_file, _p


async def _health(client) -> dict:
    return (await client.get("/api/registry/health")).json()


async def test_a_refused_file_is_reported_until_a_good_one_is_imported(client, monkeypatch, tmp_path):
    name = _p("dup")
    entry = _app_entry(name, f"pt-{name}", ["wip"], None)
    _key_file(monkeypatch, tmp_path, entry, entry)
    report = await key_import.run_pass()
    assert any("names a key twice" in s for s in report.skipped)
    assert key_import.key_file_state() == "refused"

    health = await _health(client)
    assert health["status"] == "healthy"
    assert health["key_file_import"] == "refused"
    assert "twice" not in str(health) and name not in str(health)

    _key_file(monkeypatch, tmp_path, entry)
    await key_import.run_pass()
    assert key_import.key_file_state() == "imported"
    assert (await _health(client))["key_file_import"] == "imported"


async def test_an_import_that_raises_is_reported_failed_not_imported(client, monkeypatch, tmp_path):
    name = _p("boom")
    _key_file(monkeypatch, tmp_path, _app_entry(name, f"pt-{name}", ["wip"], None))

    async def explode(*a, **kw):
        raise RuntimeError("document save failed")

    monkeypatch.setattr(key_import, "_import_keys", explode)
    with pytest.raises(RuntimeError):
        await key_import.run_pass()

    assert key_import.key_file_state() == "failed"
    assert (await _health(client))["key_file_import"] == "failed"


def test_no_finished_pass_is_pending():
    key_import.reset_for_tests()
    assert key_import.key_file_state() == "pending"
