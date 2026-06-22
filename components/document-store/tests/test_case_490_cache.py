"""CASE-490 — namespace-stamp cache coherence for the template cache.

A pinned (template_id, version) entry's schema is immutable but its `status`
is not (deactivate/reactivate). The pinned cache is bounded by a per-namespace
change stamp: polled at most once per LATEST_TTL_SECONDS per namespace, a
changed stamp evicts that namespace's cached entries so a status flip
propagates without a pod restart.

Hermetic unit tests — the stamp fetch is stubbed; no HTTP, no DB.
"""

import pytest

from document_store.services.template_store_client import TemplateStoreClient


def _tpl(tid: str, version: int, namespace: str, status: str = "active") -> dict:
    return {"template_id": tid, "version": version, "namespace": namespace, "status": status}


def _client_with(*entries: dict) -> TemplateStoreClient:
    c = TemplateStoreClient(base_url="http://unused", api_key="k")
    for t in entries:
        c._template_cache[f"{t['template_id']}:v{t['version']}"] = t
    return c


def _const(value):
    async def _f(_namespace):
        return value
    return _f


def test_evict_namespace_only_drops_matching_namespace():
    c = _client_with(_tpl("A", 1, "ns1"), _tpl("B", 1, "ns1"), _tpl("C", 1, "ns2"))
    c._latest_cache["A"] = (_tpl("A", 1, "ns1"), 123.0)
    c._latest_cache["C"] = (_tpl("C", 1, "ns2"), 123.0)

    c._evict_namespace("ns1")

    assert set(c._template_cache.keys()) == {"C:v1"}
    assert "A" not in c._latest_cache
    assert "C" in c._latest_cache


@pytest.mark.asyncio
async def test_changed_stamp_evicts_namespace(monkeypatch):
    c = _client_with(_tpl("A", 1, "ns1"))
    c._ns_stamp["ns1"] = ("1:t0", 0.0)  # known baseline, throttle expired (checked_at=0)
    monkeypatch.setattr(c, "_fetch_namespace_stamp", _const("2:t1"))

    await c._namespace_is_fresh("ns1")

    assert "A:v1" not in c._template_cache  # evicted on change


@pytest.mark.asyncio
async def test_unchanged_stamp_keeps_cache(monkeypatch):
    c = _client_with(_tpl("A", 1, "ns1"))
    c._ns_stamp["ns1"] = ("1:t0", 0.0)
    monkeypatch.setattr(c, "_fetch_namespace_stamp", _const("1:t0"))

    await c._namespace_is_fresh("ns1")

    assert "A:v1" in c._template_cache


@pytest.mark.asyncio
async def test_first_sight_records_baseline_without_evicting(monkeypatch):
    c = _client_with(_tpl("A", 1, "ns1"))  # no prior _ns_stamp entry
    monkeypatch.setattr(c, "_fetch_namespace_stamp", _const("1:t0"))

    await c._namespace_is_fresh("ns1")

    assert "A:v1" in c._template_cache          # fresh entries kept
    assert c._ns_stamp["ns1"][0] == "1:t0"      # baseline recorded


@pytest.mark.asyncio
async def test_throttled_within_ttl(monkeypatch):
    c = _client_with(_tpl("A", 1, "ns1"))
    calls = {"n": 0}

    async def _count(_ns):
        calls["n"] += 1
        return "1:t0"

    monkeypatch.setattr(c, "_fetch_namespace_stamp", _count)
    await c._namespace_is_fresh("ns1")  # polls
    await c._namespace_is_fresh("ns1")  # within TTL → no second poll

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_fetch_failure_keeps_cache(monkeypatch):
    c = _client_with(_tpl("A", 1, "ns1"))
    c._ns_stamp["ns1"] = ("1:t0", 0.0)  # had a baseline; throttle expired
    monkeypatch.setattr(c, "_fetch_namespace_stamp", _const(None))

    await c._namespace_is_fresh("ns1")

    assert "A:v1" in c._template_cache  # transient failure → keep serving cache


@pytest.mark.asyncio
async def test_no_namespace_is_a_noop(monkeypatch):
    c = _client_with(_tpl("A", 1, "ns1"))
    called = {"n": 0}

    async def _count(_ns):
        called["n"] += 1
        return "x"

    monkeypatch.setattr(c, "_fetch_namespace_stamp", _count)
    await c._namespace_is_fresh(None)

    assert called["n"] == 0
    assert "A:v1" in c._template_cache
