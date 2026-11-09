"""Key import: an empty or vanished reservations file is a bad read and
removes nothing; the shadow check runs one pass at a time, whoever calls it.
"""

import asyncio
import json
import threading
import time
import uuid

import pytest

from registry.models.reservation import RuntimeReservation
from registry.services import key_import, key_shadowing
from registry.services.reservations import RESERVATIONS_FILE_ENV
from tests.test_case_1033_key_import import _provider


@pytest.fixture
def provider():
    """The Registry's own key provider, its config keys put back after."""
    provider = _provider()
    names = provider.config_key_names()
    original = [k for k in provider.iter_keys() if k.name in names]
    key_shadowing.reset_for_tests()
    yield provider
    provider.reload_config_keys(original)
    key_shadowing.reset_for_tests()


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


async def _managed(prefix: str) -> RuntimeReservation | None:
    return await RuntimeReservation.find_one(
        RuntimeReservation.prefix == prefix, RuntimeReservation.managed_by == "wip-deploy",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_read", ["empty", "vanished"])
async def test_a_bad_reservations_read_removes_nothing(client, monkeypatch, tmp_path, bad_read):
    """The deployer rewrites the file in place: a read that lands between the
    truncate and the write sees zero bytes, and a file can vanish between the
    change check and the read. Neither is "no reservations"."""
    prefix = _p("res")
    path = tmp_path / "namespace-reservations.json"
    path.write_text(json.dumps({"reservations": [{"prefix": prefix, "app": "cooking"}]}))
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))
    await key_import.run_pass()
    assert await _managed(prefix) is not None

    if bad_read == "empty":
        path.write_text("")
    else:
        monkeypatch.setattr(key_import.os.path, "exists", lambda _p: True)
        path.unlink()
    await key_import.run_pass()
    assert await _managed(prefix) is not None


@pytest.mark.asyncio
async def test_the_shadow_check_runs_one_pass_at_a_time(client, monkeypatch, provider):
    """The import loop and a system restore both reconcile; their passes
    share one match cache, so they must not overlap."""
    running, peak = 0, 0
    lock = threading.Lock()

    def slow_compute(config_prehashes, runtime, cache):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.2)
        with lock:
            running -= 1
        return {}

    monkeypatch.setattr(key_shadowing, "compute_shadows", slow_compute)
    await asyncio.gather(key_shadowing.reconcile(provider), key_shadowing.reconcile(provider))
    assert peak == 1
