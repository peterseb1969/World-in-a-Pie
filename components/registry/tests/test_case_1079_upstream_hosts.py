"""The Registry refuses any attachment upstream host the app edge refuses
(CASE-1079).

The Registry checked the URL's shape and not its host, while the edge
accepted only letters, digits, dots and hyphens or an IP literal. So a host
like `my_app` or `bücher.example` was written, read back `served: true`, and
skipped by the edge on every poll: /apps/<name> answered 404 for as long as
the record stood. An empty or over-long label went further: the resolver's
idna codec raised UnicodeError, which no handler caught, so the whole bulk
write failed with 500 after earlier items were written. Now:

- one host rule, applied by the Registry at write time (the item fails
  with validation_failed, rule upstream_invalid) and by the edge; a contract
  test holds the two to the same verdict on every host;
- a record stored before the rule reads `served: false` with
  `not_served_reason: upstream_invalid`;
- neither resolver lets UnicodeError escape.
"""

import importlib.util
import ipaddress
import json
import sys
from pathlib import Path

import pytest

from registry.models.attachment import AppAttachment, upstream_host_error
from registry.services import attachments as attach_service

ATTACH = "/api/registry/attachments"
EDGE_SYNC = Path(__file__).resolve().parents[2] / "app-edge" / "src" / "edge_sync.py"

HOSTS = {
    # host: accepted?
    "app.example": True,
    "my-app": True,
    "a1.b2.example.": True,
    "xn--bcher-kva.example": True,
    "192.0.2.10": True,
    "2001:db8::10": True,
    "my_app": False,
    "bücher.example": False,
    "a..b.example": False,
    "-app.example": False,
    "app-.example": False,
    "x" * 64 + ".example": False,
    ".".join(["a" * 63] * 4): False,  # 255 characters
}


def _edge_sync():
    spec = importlib.util.spec_from_file_location("edge_sync_contract", EDGE_SYNC)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look the module up
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("host", "accepted"), sorted(HOSTS.items()))
def test_the_registry_and_the_edge_agree_on_every_host(host, accepted):
    edge = _edge_sync()
    assert (upstream_host_error(host) is None) is accepted
    assert (edge._host_error(host) is None) is accepted


@pytest.mark.parametrize("host", ["a..b.example", "x" * 64 + ".example"])
def test_the_edge_resolver_survives_a_name_the_codec_refuses(host):
    assert _edge_sync().resolve(host) == set()


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": [], "reserved_names": [], "app_edge": True}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    return path


@pytest.fixture
def fake_dns(monkeypatch):
    async def _resolve(host: str):
        return {ipaddress.ip_address("203.0.113.10")}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)


def _item(name: str, upstream: str) -> dict:
    return {"name": name, "upstream": upstream, "auth_required": True, "allowed_groups": []}


@pytest.mark.asyncio
async def test_a_bad_host_fails_its_item_and_nothing_else(client, auth_headers, policy):
    """Real resolution: the bad hosts are refused before anything resolves
    them, so the codec's UnicodeError never reaches the write."""
    r = await client.post(ATTACH, headers=auth_headers, json=[
        _item("aa", "http://192.0.2.10:3000"),
        _item("bb", "http://my_app:3000"),
        _item("cc", "http://a..b.example"),
        _item("dd", "https://bücher.example"),
        _item("ee", "http://192.0.2.11:3000"),
    ])
    assert r.status_code == 200, r.text
    results = {
        x["name"]: (x["status"], x.get("error_code"), (x.get("details") or {}).get("rule"))
        for x in r.json()["results"]
    }
    assert results["aa"][0] == results["ee"][0] == "created"
    for name in ("bb", "cc", "dd"):
        assert results[name] == ("error", "validation_failed", "upstream_invalid"), results[name]


@pytest.mark.asyncio
async def test_the_registry_resolver_survives_a_name_the_codec_refuses():
    assert await attach_service._resolve("a..b.example") == set()


@pytest.mark.asyncio
async def test_a_record_stored_before_the_rule_reads_not_served(
    client, auth_headers, policy, fake_dns,
):
    r = await client.post(ATTACH, headers=auth_headers, json=[_item("aa", "http://good-host:3000")])
    assert r.json()["results"][0]["status"] == "created", r.text
    await AppAttachment.find_one(AppAttachment.name == "aa").update(
        {"$set": {"upstream": "http://my_app:3000"}}
    )
    one = (await client.get(f"{ATTACH}/aa", headers=auth_headers)).json()
    assert (one["served"], one["not_served_reason"]) == (False, "upstream_invalid")
