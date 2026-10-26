"""CASE-951 — a stale or incomplete secret store never reaches a running install.

`ensure_secrets` generated a value for every required secret without a file.
On an install whose datastores already ran, a secrets/ folder copied from an
older snapshot therefore minted fresh MongoDB/NATS/PostgreSQL/Garage
passwords, and the apply wrote them into the cluster Secret while the pods
kept the old ones — an outage waiting for the next restart. A scoped verb
(add-app) made it latent: it applied the shared Secret but restarted only its
own app.

Contract under test:
  - every apply records a digest per secret it ran with (deployer-state
    `secret_digests`); the next apply refuses a known secret that is missing
    from the store or holds a different value, before generating anything;
  - on k8s the live `wip-secrets` is a second source of known values and wins;
  - operator-supplied secrets, derived bcrypt hashes and the secret a verb
    regenerates on purpose (rotate-key) are exempt;
  - `secrets pull` adopts the live values, `secrets accept` records the store;
  - an apply names the secrets it generated;
  - a scoped verb also restarts out-of-scope services whose configuration
    changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer
import yaml
from typer.testing import CliRunner

from wip_deploy.apply import changed_outside_scope
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import (
    _apply_and_persist_mutation,
    _ensure_secrets_via_spec,
    _load_deployment,
    _persist_deployment,
    app,
)
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.base import FileTree
from wip_deploy.secrets import SecretDrift, ensure_secrets, secret_digest
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import Deployment

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(tmp_path: Path, target: str = "compose") -> Deployment:
    d = build_deployment(
        BuildInputs(
            target=target,  # type: ignore[arg-type]
            variant="dev" if target == "compose" else "prod",
            hostname="localhost" if target == "compose" else "wip.example",
            tls="internal",
            compose_data_dir=tmp_path / "data",
            k8s_namespace="wip-test",
            registry="ghcr.io/peterseb1969",
        )
    )
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    return d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})


def _applied(tmp_path: Path, discovery: Discovery, target: str = "compose") -> Deployment:
    """An install that has had one apply: its store exists and the state
    records what it ran with."""
    d = _deployment(tmp_path, target)
    s = _ensure_secrets_via_spec(d, discovery.components, discovery.apps, install_dir=tmp_path)
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT, secrets=s)
    return d


def _ensure(d: Deployment, discovery: Discovery, tmp_path: Path, **kw: Any) -> Any:
    return _ensure_secrets_via_spec(
        d, discovery.components, discovery.apps, install_dir=tmp_path, **kw,
    )


class TestEnsureSecrets:
    def test_known_secret_missing_is_refused_before_generating(
        self, tmp_path: Path, discovery: Discovery,
    ) -> None:
        d = _deployment(tmp_path)
        backend = FileSecretBackend(tmp_path / "s")
        first = ensure_secrets(d, discovery.components, discovery.apps, backend)
        known = {n: secret_digest(v) for n, v in first.values.items()}
        (tmp_path / "s" / "mongo-root-password").unlink()

        with pytest.raises(SecretDrift) as e:
            ensure_secrets(
                d, discovery.components, discovery.apps,
                FileSecretBackend(tmp_path / "s"), known=known,
            )
        assert e.value.missing == ["mongo-root-password"]
        assert not (tmp_path / "s" / "mongo-root-password").exists()

    def test_known_secret_changed_is_refused(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _deployment(tmp_path)
        first = ensure_secrets(d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"))
        known = {n: secret_digest(v) for n, v in first.values.items()}
        (tmp_path / "s" / "api-key").write_text("an-older-key")

        with pytest.raises(SecretDrift) as e:
            ensure_secrets(
                d, discovery.components, discovery.apps,
                FileSecretBackend(tmp_path / "s"), known=known,
            )
        assert e.value.changed == ["api-key"]

    def test_regenerate_and_bcrypt_are_exempt(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _deployment(tmp_path)
        first = ensure_secrets(d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"))
        known = {n: secret_digest(v) for n, v in first.values.items()}
        (tmp_path / "s" / "api-key").unlink()
        (tmp_path / "s" / "dex-password-admin.bcrypt").unlink()

        again = ensure_secrets(
            d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"),
            known=known, regenerate=frozenset({"api-key"}),
        )
        assert again.generated == ("api-key", "dex-password-admin.bcrypt")

    def test_new_secret_is_generated_and_reported(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _deployment(tmp_path)
        first = ensure_secrets(d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"))
        assert "mongo-root-password" in first.generated
        known = {n: secret_digest(v) for n, v in first.values.items() if n != "api-key"}
        (tmp_path / "s" / "api-key").unlink()
        # Not known (as after an upgrade that adds a credential): generated.
        again = ensure_secrets(
            d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"), known=known,
        )
        assert again.generated == ("api-key",)


class TestRecordedDigests:
    def test_apply_records_digests_not_values(self, tmp_path: Path, discovery: Discovery) -> None:
        _applied(tmp_path, discovery)
        state = json.loads((tmp_path / "deployment.deployer-state").read_text())
        digests = state["secret_digests"]
        value = (tmp_path / "secrets" / "mongo-root-password").read_text()
        assert digests["mongo-root-password"] == secret_digest(value)
        assert value not in json.dumps(state)

    def test_persist_without_secrets_carries_the_record_forward(
        self, tmp_path: Path, discovery: Discovery,
    ) -> None:
        d = _applied(tmp_path, discovery)
        before = json.loads((tmp_path / "deployment.deployer-state").read_text())["secret_digests"]
        _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
        after = json.loads((tmp_path / "deployment.deployer-state").read_text())["secret_digests"]
        assert after == before

    def test_stale_store_stops_the_apply(
        self, tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str],
    ) -> None:
        d = _applied(tmp_path, discovery)
        (tmp_path / "secrets" / "nats-password-def-store").unlink()
        (tmp_path / "secrets" / "api-key").write_text("an-older-key")

        with pytest.raises(typer.Exit) as e:
            _ensure(d, discovery, tmp_path)
        assert e.value.exit_code == 2
        err = capsys.readouterr().err
        assert "nats-password-def-store" in err
        assert "api-key" in err
        assert "secrets accept" in err
        assert not (tmp_path / "secrets" / "nats-password-def-store").exists()

    def test_unchanged_store_passes_and_generates_nothing(
        self, tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str],
    ) -> None:
        d = _applied(tmp_path, discovery)
        capsys.readouterr()
        s = _ensure(d, discovery, tmp_path)
        assert s.generated == ()
        assert "Generated new secrets" not in capsys.readouterr().out


class TestLiveSecret:
    def _live(self, tmp_path: Path) -> dict[str, str]:
        return {p.name: p.read_text() for p in (tmp_path / "secrets").iterdir()}

    def test_live_secret_catches_a_store_without_a_record(
        self, tmp_path: Path, discovery: Discovery,
    ) -> None:
        d = _deployment(tmp_path, "k8s")
        ensure_secrets(d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "secrets"))
        live = self._live(tmp_path)
        (tmp_path / "secrets" / "mongo-password-registry").unlink()

        with (
            patch("wip_deploy.cli.read_live_secrets", return_value=live),
            pytest.raises(typer.Exit),
        ):
            _ensure(d, discovery, tmp_path)  # no deployer-state at all

    def test_live_secret_wins_over_the_record(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _applied(tmp_path, discovery, "k8s")
        live = self._live(tmp_path)
        # The cluster was since driven from elsewhere with a newer key, and
        # this store already holds it: the stale record must not refuse it.
        live["api-key"] = "the-newer-key"
        (tmp_path / "secrets" / "api-key").write_text("the-newer-key")
        with patch("wip_deploy.cli.read_live_secrets", return_value=live):
            _ensure(d, discovery, tmp_path)

    def test_pull_writes_missing_and_different_values(
        self, tmp_path: Path, discovery: Discovery,
    ) -> None:
        _applied(tmp_path, discovery, "k8s")
        live = self._live(tmp_path)
        (tmp_path / "secrets" / "mongo-root-password").unlink()
        (tmp_path / "secrets" / "api-key").write_text("an-older-key")

        with patch("wip_deploy.cli.read_live_secrets", return_value=live):
            result = runner.invoke(app, ["secrets", "pull", "--install-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "api-key, mongo-root-password" in result.output
        assert (tmp_path / "secrets" / "mongo-root-password").read_text() == live["mongo-root-password"]
        assert (tmp_path / "secrets" / "api-key").read_text() == live["api-key"]
        assert live["api-key"] not in result.output

    def test_pull_refuses_compose(self, tmp_path: Path, discovery: Discovery) -> None:
        _applied(tmp_path, discovery)
        result = runner.invoke(app, ["secrets", "pull", "--install-dir", str(tmp_path)])
        assert result.exit_code == 2


def test_accept_records_the_store(tmp_path: Path, discovery: Discovery) -> None:
    d = _applied(tmp_path, discovery)
    (tmp_path / "secrets" / "api-key").write_text("restored-from-backup")
    result = runner.invoke(
        app, ["secrets", "accept", "--install-dir", str(tmp_path), "--repo-root", str(REPO_ROOT)],
    )
    assert result.exit_code == 0, result.output
    _ensure(_load_deployment(tmp_path), discovery, tmp_path)  # no longer refused
    assert d.metadata.name in result.output


def test_rotate_key_regenerates_its_secret(tmp_path: Path, discovery: Discovery) -> None:
    _applied(tmp_path, discovery)
    old = (tmp_path / "secrets" / "api-key").read_text()
    with patch("wip_deploy.cli.apply_compose") as apply:
        apply.return_value = MagicMock(healthy=True, post_install_skipped=[])
        result = runner.invoke(
            app,
            ["rotate-key", "--secret", "api-key", "--no-check",
             "--install-dir", str(tmp_path), "--repo-root", str(REPO_ROOT)],
        )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "secrets" / "api-key").read_text() != old


class TestScopeWidening:
    def _compose(self, hashes: dict[str, str]) -> str:
        return yaml.safe_dump({"services": {
            n: {"image": "x", "labels": {CONFIG_HASH_KEY: h}} for n, h in hashes.items()
        }})

    def test_compose_names_changed_out_of_scope_services(self, tmp_path: Path) -> None:
        (tmp_path / "docker-compose.yaml").write_text(
            self._compose({"registry": "a", "def-store": "b", "wip-kb": "c"})
        )
        tree = FileTree()
        tree.add("docker-compose.yaml", self._compose({"registry": "A", "def-store": "b", "wip-kb": "C"}))
        assert changed_outside_scope(tree, tmp_path, "compose", ["wip-kb"]) == ["registry"]

    def test_k8s_names_changed_out_of_scope_workloads(self, tmp_path: Path) -> None:
        def workload(name: str, h: str, kind: str = "Deployment") -> str:
            return yaml.safe_dump({
                "kind": kind, "metadata": {"name": f"wip-{name}"},
                "spec": {"template": {"metadata": {"annotations": {CONFIG_HASH_KEY: h}}}},
            })

        (tmp_path / "services").mkdir()
        (tmp_path / "infrastructure").mkdir()
        (tmp_path / "services" / "router.yaml").write_text(workload("router", "r1"))
        (tmp_path / "services" / "wip-kb.yaml").write_text(workload("wip-kb", "k1"))
        (tmp_path / "infrastructure" / "mongodb.yaml").write_text(workload("mongodb", "m1", "StatefulSet"))
        tree = FileTree()
        tree.add("services/router.yaml", workload("router", "r2"))
        tree.add("services/wip-kb.yaml", workload("wip-kb", "k2"))
        tree.add("infrastructure/mongodb.yaml", workload("mongodb", "m2", "StatefulSet"))
        tree.add("services/new-app.yaml", workload("new-app", "n1"))
        assert changed_outside_scope(tree, tmp_path, "k8s", ["wip-kb"]) == ["mongodb", "router"]

    def test_scoped_verb_widens_its_apply(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _applied(tmp_path, discovery)
        with (
            patch("wip_deploy.cli.changed_outside_scope", return_value=["router"]),
            patch("wip_deploy.cli.apply_compose") as apply,
        ):
            apply.return_value = MagicMock(healthy=True, post_install_skipped=[])
            _apply_and_persist_mutation(
                d, discovery.components, discovery.apps, tmp_path, "label",
                services_scope=["wip-kb"], repo_root=REPO_ROOT,
            )
        assert apply.call_args.kwargs["services_scope"] == ["wip-kb", "router"]
