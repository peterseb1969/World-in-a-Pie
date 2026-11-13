"""`verify --security --live` on a k8s install reads its trust anchor from the
cluster the install records.

A self-signed k8s install is verified against the certificate in its TLS
Secret, which the deployer reads with kubectl. verify is a fresh process: no
cluster is selected until the verb selects the install's own, so reading the
anchor before that selection crashed with "a kubectl call before the
install's cluster was selected". The unit conftest selects the shell's
context for every test, which hid it; this test clears that selection.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wip_deploy import kube
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.tls_trust import Untrusted

from ._kube import unit_cluster

runner = CliRunner()


def _k8s_install(tmp_path: Path) -> None:
    d = build_deployment(BuildInputs(
        target="k8s", variant="dev", hostname="wip.example", tls="self-signed",
        k8s_namespace="wip-test", registry="ghcr.io/peterseb1969",
    ))
    assert d.spec.platform.k8s is not None
    d.spec.platform.k8s.kube = unit_cluster()
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    (tmp_path / "secrets").mkdir(mode=0o700)
    (tmp_path / "secrets" / "api-key").write_text("kR7mX2pQ9vL4nB8wZ3cF6a")
    _persist_deployment(d, tmp_path, repo_root=None)


def test_live_verify_selects_the_install_cluster_before_reading_its_anchor(tmp_path: Path):
    _k8s_install(tmp_path)
    argv: list[list[str]] = []

    def anchor_from_cluster(_deployment):
        argv.append(kube.kubectl("get", "secret"))
        return Untrusted("stub: no certificate read in a unit test")

    kube.reset_for_tests()  # a fresh CLI process has selected no cluster
    with patch("wip_deploy.tls_trust.trust_anchor", side_effect=anchor_from_cluster):
        result = runner.invoke(
            app, ["verify", "--security", "--live", "--install-dir", str(tmp_path)]
        )

    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    assert argv == [["kubectl", "--context", "unit-test", "get", "secret"]]
    assert "never sent unverified" in result.output
