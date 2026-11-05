"""An attached app's upstream may not be one of the cluster's own node
addresses (CASE-1062): there the API server and the kubelets listen. The
deployer renders the node ranges into the attach policy as blocked_cidrs."""

import ipaddress
import json

import pytest

from registry.services import attachments as attach_service

POLICY = attach_service.AttachPolicy(blocked_cidrs=["192.168.1.20/32", "fd00:20::/64"])


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    names = {"apiserver.example": {"192.168.1.20"}, "aa.example": {"192.168.1.30"},
             "v6.example": {"fd00:20::5"}}

    async def _resolve(host):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address(a) for a in names.get(host, set())}

    monkeypatch.setattr(attach_service, "_resolve", _resolve)


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", [
    "https://192.168.1.20:6443", "http://apiserver.example:10250", "http://v6.example",
    "http://[::ffff:192.168.1.20]:16443",
])
async def test_an_upstream_in_a_node_range_is_refused(upstream):
    check = await attach_service.check_upstream(upstream, POLICY, {})
    assert check.blocked and "node range" in check.blocked


@pytest.mark.asyncio
async def test_another_address_on_the_lan_is_accepted():
    """A NodePort another cluster serves on the same LAN stays attachable."""
    assert (await attach_service.check_upstream("http://aa.example:30080", POLICY, {})).blocked is None


def test_a_policy_with_a_bad_range_is_unusable(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": [], "reserved_names": [], "blocked_cidrs": ["nope"]}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    with pytest.raises(attach_service.AttachPolicyUnavailable):
        attach_service.load_policy()


def test_a_policy_without_ranges_still_loads(tmp_path, monkeypatch):
    path = tmp_path / "attach-policy.json"
    path.write_text(json.dumps({"blocked_hosts": ["wip-registry"], "reserved_names": []}))
    monkeypatch.setenv(attach_service.ATTACH_POLICY_FILE_ENV, str(path))
    assert attach_service.load_policy().blocked_cidrs == []
