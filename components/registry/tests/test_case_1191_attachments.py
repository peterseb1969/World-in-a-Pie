"""Attachments: an upstream on port 0 is refused (it would be dialled on the
scheme's default port), and an attachment name is a DNS label — the app is
served at https://<name>.<hostname>/ — so it cannot end with a hyphen. The
Registry and the app edge apply one name rule.
"""

import ipaddress
import json

import pytest

from registry.models.attachment import NAME_PATTERN
from registry.services import attachments as attach_service
from registry.services.attachments import UpstreamInvalid, parse_upstream
from tests.test_case_1051_attachments import URL, _item
from tests.test_case_1079_upstream_hosts import _edge_sync


@pytest.fixture
def policy(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": [], "reserved_names": []}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))

    async def _resolve(host: str):
        return {ipaddress.ip_address("203.0.113.20")}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)
    return path


def test_port_zero_is_refused():
    with pytest.raises(UpstreamInvalid, match="port"):
        parse_upstream("http://app.example:0")


def test_an_explicit_port_is_kept():
    assert parse_upstream("http://app.example:8080") == ("app.example", 8080)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["ab-", "a-"])
async def test_a_name_ending_with_a_hyphen_is_refused(client, auth_headers, policy, name):
    r = await client.post(URL, json=[_item(name=name)], headers=auth_headers)
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_a_name_of_inner_hyphens_is_accepted(client, auth_headers, policy):
    r = await client.post(URL, json=[_item(name="a-b")], headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] in ("created", "updated"), r.text


def test_the_registry_and_the_edge_share_one_name_rule():
    assert _edge_sync().NAME_RE.pattern == NAME_PATTERN
