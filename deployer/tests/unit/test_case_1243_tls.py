"""CASE-1243 (7-secrets R5): the install's master key is never sent unverified.

The pre-apply checks and the live and rotation checks sent the master key with
TLS verification off on every install but `letsencrypt`. They now verify
against the install's own trust anchor, and when there is none they skip the
request and say so.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

import wip_deploy.cli as cli
from wip_deploy.attachment_check import check_attachment_collisions
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.discovery import discover
from wip_deploy.export_ca import ExportCAError
from wip_deploy.tls_trust import Untrusted, anchor_path, trust_anchor

PEM = b"-----BEGIN CERTIFICATE-----\nQUFBQQ==\n-----END CERTIFICATE-----\n"


def _deployment(target: str = "compose", tls: str | None = None):
    d = build_deployment(BuildInputs(
        name="t", preset="standard", target=target, hostname="wip.example.org",
        compose_data_dir=Path("/tmp/x"), k8s_namespace="wip-t", apps=["wip-kb"],
    ))
    if tls is not None:
        network = d.spec.network.model_copy(update={"tls": tls})
        d = d.model_copy(update={"spec": d.spec.model_copy(update={"network": network})})
    return d


@pytest.mark.parametrize("tls", ["letsencrypt", "external"])
def test_public_certificates_use_the_system_store(tls: str) -> None:
    assert trust_anchor(_deployment(tls=tls)) is True


def test_internal_uses_the_installs_caddy_ca() -> None:
    with patch("wip_deploy.export_ca.export_caddy_internal_ca", return_value=PEM) as export:
        verify = trust_anchor(_deployment())
    assert export.call_args.args[0] == "t_wip-caddy"
    path = anchor_path(verify)
    assert path is not None and path.read_bytes() == PEM
    path.unlink()


def test_internal_without_a_ca_yet_is_untrusted() -> None:
    with patch("wip_deploy.export_ca.export_caddy_internal_ca",
               side_effect=ExportCAError("container 't_wip-caddy' is not running.")):
        verify = trust_anchor(_deployment())
    assert isinstance(verify, Untrusted) and "not running" in verify.why


def test_self_signed_uses_the_installs_tls_secret() -> None:
    proc = MagicMock(returncode=0, stdout=base64.b64encode(PEM).decode())
    with patch("wip_deploy.tls_trust.subprocess.run", return_value=proc) as run, \
         patch("wip_deploy.kube.kubectl", return_value=["kubectl"]):
        verify = trust_anchor(_deployment("k8s"))
    assert run.call_args.args[0][:5] == ["kubectl", "get", "secret", "wip-tls", "-n"]
    path = anchor_path(verify)
    assert path is not None and path.read_bytes() == PEM
    path.unlink()


def test_an_unreadable_tls_secret_is_untrusted() -> None:
    with patch("wip_deploy.tls_trust.subprocess.run",
               side_effect=subprocess.TimeoutExpired(["kubectl"], 30)), \
         patch("wip_deploy.kube.kubectl", return_value=["kubectl"]):
        assert isinstance(trust_anchor(_deployment("k8s")), Untrusted)


def test_without_an_anchor_the_key_is_not_sent(capsys: pytest.CaptureFixture[str]) -> None:
    secrets = MagicMock(values={"api-key": "the-master-key"})
    with patch("wip_deploy.tls_trust.trust_anchor", return_value=Untrusted("no CA yet")), \
         patch("wip_deploy.attachment_check.check_attachment_collisions") as check:
        cli._refuse_attachment_collisions(_deployment(), [], secrets)
    check.assert_not_called()
    assert "not checked: no CA yet; the admin key is never sent unverified" in capsys.readouterr().err


def test_the_anchor_reaches_the_request_and_is_removed_after(tmp_path: Path) -> None:
    anchor = tmp_path / "ca.crt"
    anchor.write_bytes(PEM)
    seen: list[object] = []

    def check(*a, verify=None, **k):
        seen.append(verify)
        from wip_deploy.attachment_check import AttachmentCheck
        return AttachmentCheck()

    secrets = MagicMock(values={"api-key": "k"})
    with patch("wip_deploy.tls_trust.trust_anchor", return_value=str(anchor)), \
         patch("wip_deploy.attachment_check.check_attachment_collisions", side_effect=check):
        cli._refuse_attachment_collisions(_deployment(), [], secrets)
    assert seen == [str(anchor)]
    assert not anchor.exists()


def test_the_attachment_check_verifies_with_what_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    real = httpx.Client

    def recording(*a, **k):
        seen.append(k.get("verify"))
        return real(*a, **k)

    monkeypatch.setattr(httpx, "Client", recording)
    disc = discover(Path(__file__).resolve().parents[3])
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"items": [], "pages": 1}))
    check_attachment_collisions(_deployment(), disc.apps, "k", verify="/ca.crt", transport=transport)
    assert seen == ["/ca.crt"]
