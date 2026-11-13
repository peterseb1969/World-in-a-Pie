"""A refused key file is reported, not only logged.

A key file that names a key twice is refused whole: nothing in it is imported,
the previous import keeps serving and the Registry stays healthy, so every
apply reports success while no key change takes effect. The health answer now
says that the last pass refused the file (never why: the reason names paths and
keys, and the endpoint is public), and the next good import clears it.
"""

from __future__ import annotations

from registry.services import key_import
from tests.test_case_1033_key_import import _app_entry, _key_file, _p


async def test_a_refused_file_is_reported_until_a_good_one_is_imported(client, monkeypatch, tmp_path):
    name = _p("dup")
    entry = _app_entry(name, f"pt-{name}", ["wip"], None)
    _key_file(monkeypatch, tmp_path, entry, entry)
    report = await key_import.run_pass()
    assert any("names a key twice" in s for s in report.skipped)
    assert key_import.key_file_refused()

    health = (await client.get("/api/registry/health")).json()
    assert health["status"] == "healthy"
    assert health["key_file_refused"] is True
    assert "twice" not in str(health) and name not in str(health)

    _key_file(monkeypatch, tmp_path, entry)
    await key_import.run_pass()
    assert not key_import.key_file_refused()
    assert (await client.get("/api/registry/health")).json()["key_file_refused"] is False
