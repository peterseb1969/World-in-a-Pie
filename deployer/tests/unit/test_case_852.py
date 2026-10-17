"""CASE-852 — NATS requires authentication; each service is its own NATS user.

NATS ran with no authentication on every target: any workload on the
network could read every event, forge events, delete the streams, and —
through the ingest gateway, which forwards work items with the platform's
admin key — have documents written. Components now declare a `nats_client`
block; the deployer renders one NATS user per active client, limited to the
declared subjects, generates its password, injects the credentials, and
renders the server config with the JetStream store on NATS's volume (it
used /tmp inside the container) and the unauthenticated monitor on
127.0.0.1.

Contract under test: the rendered config carries exactly the active
clients with their declared permissions and never a secret value; the
credentials reach both sides; every target mounts the config and probes
health from inside the container; and a permission change restarts NATS.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from wip_deploy.config_gen import make_spec_context, resolve_all_env
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY
from wip_deploy.config_gen.env import Literal, SecretRef
from wip_deploy.config_gen.nats import (
    NATS_CONFIG_RENDER_PATH,
    client_permissions,
    nats_clients,
    password_secret,
    render_nats_conf,
    server_password_env,
)
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple, render_k8s
from wip_deploy.secrets import collect_required_secrets, ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AppRef,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DevPlatform,
    ImagesSpec,
    K8sPlatform,
    NatsClientSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.verify_security import check_nats_authentication

REPO_ROOT = Path(__file__).resolve().parents[3]

_CLIENTS = {"def-store", "document-store", "ingest-gateway", "reporting-sync", "template-store"}


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str = "compose", modules: list[str] | None = None) -> Deployment:
    platform = {
        "compose": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        "dev": PlatformSpec(dev=DevPlatform(mode="simple")),
        "k8s": PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
    }[target]
    return Deployment(
        metadata=DeploymentMetadata(name="case852"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": modules if modules is not None else ["reporting-sync", "ingest-gateway"]},  # type: ignore[arg-type]
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _secrets(tmp_path: Path, d: Deployment, disc: Discovery) -> ResolvedSecrets:
    return ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "s"))


def _conf(d: Deployment, disc: Discovery) -> str:
    conf = render_nats_conf(d, disc.components, disc.apps)
    assert conf is not None
    return conf


def _with_publish(disc: Discovery, component: str, extra: str) -> Discovery:
    """A copy of `disc` whose `component` may publish one more subject."""
    comps = []
    for c in disc.components:
        if c.metadata.name == component:
            assert c.spec.nats_client is not None
            nc = c.spec.nats_client.model_copy(update={"publish": [*c.spec.nats_client.publish, extra]})
            c = c.model_copy(update={"spec": c.spec.model_copy(update={"nats_client": nc})})
        comps.append(c)
    return Discovery(components=comps, apps=disc.apps, errors=disc.errors)


class TestRenderedConfig:
    def test_one_user_per_active_client(self, discovery: Discovery) -> None:
        conf = _conf(_deployment(), discovery)
        assert set(re.findall(r'user: "([^"]+)"', conf)) == _CLIENTS

    def test_inactive_clients_get_no_user(self, discovery: Discovery) -> None:
        conf = _conf(_deployment(modules=["reporting-sync"]), discovery)
        assert "ingest-gateway" not in set(re.findall(r'user: "([^"]+)"', conf))

    def test_passwords_are_env_references_never_values(
        self, tmp_path: Path, discovery: Discovery
    ) -> None:
        d = _deployment()
        conf = _conf(d, discovery)
        for client in _CLIENTS:
            assert f"password: ${server_password_env(client)}," in conf
        for value in _secrets(tmp_path, d, discovery).values.values():
            assert value not in conf

    def test_each_user_gets_its_declared_subjects_and_own_inbox(self, discovery: Discovery) -> None:
        conf = _conf(_deployment(), discovery)
        for name, spec in nats_clients(_deployment(), discovery.components, discovery.apps):
            block = conf.split(f'user: "{name}"', 1)[1].split("} }", 1)[0]
            for subject in spec.publish:
                assert f'"{subject}"' in block.split("deny:")[0]
            assert f'"_INBOX_{name}.>"' in block
            assert block.count("_INBOX_") == 1  # no shared inbox grant

    def test_document_store_cannot_delete_the_platform_streams(self, discovery: Discovery) -> None:
        conf = _conf(_deployment(), discovery)
        block = conf.split('user: "document-store"', 1)[1].split("} }", 1)[0]
        deny = block.split("deny:", 1)[1].split("]", 1)[0]
        for stream in ("WIP_EVENTS", "WIP_INGEST", "WIP_INGEST_RESULTS"):
            assert f'"$JS.API.STREAM.DELETE.{stream}"' in deny

    def test_only_document_store_may_delete_streams_at_all(self, discovery: Discovery) -> None:
        for c in discovery.components:
            if c.spec.nats_client is None or c.metadata.name == "document-store":
                continue
            assert not any(".STREAM.DELETE." in s or ".STREAM.PURGE." in s for s in c.spec.nats_client.publish)

    def test_jetstream_store_on_the_volume_and_monitor_on_localhost(self, discovery: Discovery) -> None:
        conf = _conf(_deployment(), discovery)
        nats = next(c for c in discovery.components if c.metadata.name == "nats")
        assert f'store_dir: "{nats.spec.storage[0].mount_path}"' in conf
        assert "http: 127.0.0.1:8222" in conf

    def test_render_is_deterministic(self, discovery: Discovery) -> None:
        assert _conf(_deployment(), discovery) == _conf(_deployment(), discovery)

    def test_no_nats_no_config(self, discovery: Discovery) -> None:
        d = _deployment(modules=[])
        assert render_nats_conf(d, discovery.components, discovery.apps) is None
        assert nats_clients(d, discovery.components, discovery.apps) == []


class TestCredentials:
    def test_every_client_password_is_collected(self, discovery: Discovery) -> None:
        names = collect_required_secrets(_deployment(), discovery.components, discovery.apps)
        assert {password_secret(c) for c in _CLIENTS} <= names

    def test_no_nats_no_nats_secrets(self, discovery: Discovery) -> None:
        names = collect_required_secrets(_deployment(modules=[]), discovery.components, discovery.apps)
        assert not any(n.startswith("nats-password-") for n in names)

    def test_clients_and_server_receive_the_credentials(self, discovery: Discovery) -> None:
        d = _deployment()
        env = resolve_all_env(
            d, discovery.components, discovery.apps, make_spec_context(d, discovery.components)
        )
        for client in _CLIENTS:
            merged = env[client].merged()
            assert merged["NATS_USER"] == Literal(value=client)
            assert merged["NATS_PASSWORD"] == SecretRef(name=password_secret(client))
            assert env["nats"].merged()[server_password_env(client)] == SecretRef(
                name=password_secret(client)
            )

    def test_react_console_is_a_nats_monitor(self, discovery: Discovery) -> None:
        """RC's NATS page lists streams and consumers: the read-only role,
        with NATS_URL and credentials injected rather than declared."""
        rc = next(a for a in discovery.apps if a.metadata.name == "react-console")
        assert rc.spec.nats_client is not None
        assert rc.spec.nats_client.roles == ["nats-monitor"]
        assert not rc.spec.nats_client.raw
        d = _with_rc()
        env = resolve_all_env(d, discovery.components, discovery.apps, make_spec_context(d, discovery.components))
        merged = env["react-console"].merged()
        assert merged["NATS_USER"] == Literal(value="react-console")
        assert "NATS_URL" in merged and "NATS_PASSWORD" in merged


def _compose_doc(target: str, tmp_path: Path, disc: Discovery) -> tuple[dict[str, Any], Any]:
    d = _deployment(target)
    s = _secrets(tmp_path, d, disc)
    tree = (
        render_compose(d, disc.components, disc.apps, s)
        if target == "compose"
        else render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT)
    )
    return yaml.safe_load(tree.files[Path("docker-compose.yaml")].content), tree


@pytest.mark.parametrize("target", ["compose", "dev"])
class TestComposeTargets:
    def test_config_written_without_secrets_and_mounted(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        doc, tree = _compose_doc(target, tmp_path, discovery)
        entry = tree.files[Path(NATS_CONFIG_RENDER_PATH)]
        assert entry.mode == 0o644
        nats = doc["services"]["nats"]
        assert f"./{NATS_CONFIG_RENDER_PATH}:/etc/nats/nats.conf:ro" in nats["volumes"]
        assert nats["entrypoint"] == ["nats-server"]
        assert nats["command"] == ["-c", "/etc/nats/nats.conf"]

    def test_probe_runs_inside_and_monitor_is_not_published(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        doc, _ = _compose_doc(target, tmp_path, discovery)
        nats = doc["services"]["nats"]
        assert "127.0.0.1:8222/healthz" in nats["healthcheck"]["test"][1]
        assert "ports" not in nats

    def test_permission_change_relabels_nats(
        self, target: str, tmp_path: Path, discovery: Discovery
    ) -> None:
        before, _ = _compose_doc(target, tmp_path, discovery)
        after, _ = _compose_doc(target, tmp_path, _with_publish(discovery, "def-store", "wip.extra.>"))
        label = lambda doc: doc["services"]["nats"]["labels"][CONFIG_HASH_KEY]  # noqa: E731
        assert label(before) != label(after)
        again, _ = _compose_doc(target, tmp_path, discovery)
        assert label(before) == label(again)


class TestK8s:
    def _docs(self, tmp_path: Path, disc: Discovery) -> list[dict[str, Any]]:
        d = _deployment("k8s")
        tree = render_k8s(d, disc.components, disc.apps, _secrets(tmp_path, d, disc))
        return [doc for e in tree.files.values() for doc in yaml.safe_load_all(e.content) if doc]

    def _nats(self, docs: list[dict[str, Any]]) -> dict[str, Any]:
        return next(d for d in docs if d["kind"] == "StatefulSet" and d["metadata"]["name"] == "wip-nats")

    def test_configmap_mounted_as_the_config_directory(self, tmp_path: Path, discovery: Discovery) -> None:
        docs = self._docs(tmp_path, discovery)
        cm = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "wip-nats-config")
        assert cm["data"]["nats.conf"] == _conf(_deployment("k8s"), discovery)
        pod = self._nats(docs)["spec"]["template"]["spec"]
        container = pod["containers"][0]
        assert {"name": "config", "mountPath": "/etc/nats", "readOnly": True} in container["volumeMounts"]
        assert {"name": "config", "configMap": {"name": "wip-nats-config"}} in pod["volumes"]
        assert container["command"] == ["nats-server", "-c", "/etc/nats/nats.conf"]

    def test_exec_probe_and_no_monitor_port(self, tmp_path: Path, discovery: Discovery) -> None:
        docs = self._docs(tmp_path, discovery)
        container = self._nats(docs)["spec"]["template"]["spec"]["containers"][0]
        assert "exec" in container["readinessProbe"]
        svc = next(d for d in docs if d["kind"] == "Service" and d["metadata"]["name"] == "wip-nats")
        assert [p["port"] for p in svc["spec"]["ports"]] == [4222]

    def test_permission_change_rolls_nats(self, tmp_path: Path, discovery: Discovery) -> None:
        ann = lambda docs: self._nats(docs)["spec"]["template"]["metadata"]["annotations"][CONFIG_HASH_KEY]  # noqa: E731
        before = ann(self._docs(tmp_path, discovery))
        after = ann(self._docs(tmp_path, _with_publish(discovery, "def-store", "wip.extra.>")))
        assert before != after


class TestSpec:
    @pytest.mark.parametrize("subject", ["", "wip. documents", 'wip."x"'])
    def test_rejects_malformed_subjects(self, subject: str) -> None:
        with pytest.raises(ValidationError):
            NatsClientSpec(publish=[subject])

    def test_requires_at_least_one_publish_subject(self) -> None:
        with pytest.raises(ValidationError):
            NatsClientSpec(publish=[])


class TestVerifySecurity:
    """`wip-deploy verify --security` fails an install whose NATS accepts
    anonymous clients — i.e. one rendered before this change."""

    def _install(self, target: str, tmp_path: Path, disc: Discovery, modules: list[str] | None = None) -> tuple[Path, Deployment]:
        d = _deployment(target, modules)
        s = _secrets(tmp_path, d, disc)
        tree = {
            "compose": lambda: render_compose(d, disc.components, disc.apps, s),
            "dev": lambda: render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT),
            "k8s": lambda: render_k8s(d, disc.components, disc.apps, s),
        }[target]()
        root = tmp_path / "install"
        tree.write(root)
        return root, d

    @pytest.mark.parametrize("target", ["compose", "dev", "k8s"])
    def test_fresh_render_passes(self, target: str, tmp_path: Path, discovery: Discovery) -> None:
        root, d = self._install(target, tmp_path, discovery)
        result = check_nats_authentication(root, d)
        assert result.passed, result.message
        assert f"{len(_CLIENTS)} authenticated NATS users" in result.message

    @pytest.mark.parametrize("target", ["compose", "dev"])
    def test_config_without_authorization_fails(self, target: str, tmp_path: Path, discovery: Discovery) -> None:
        root, d = self._install(target, tmp_path, discovery)
        (root / NATS_CONFIG_RENDER_PATH).write_text("listen: 0.0.0.0:4222\njetstream {}\n")
        assert not check_nats_authentication(root, d).passed

    def test_missing_config_fails(self, tmp_path: Path, discovery: Discovery) -> None:
        root, d = self._install("compose", tmp_path, discovery)
        (root / NATS_CONFIG_RENDER_PATH).unlink()
        assert not check_nats_authentication(root, d).passed

    def test_k8s_without_the_configmap_fails(self, tmp_path: Path, discovery: Discovery) -> None:
        root, d = self._install("k8s", tmp_path, discovery)
        cms = [doc for doc in yaml.safe_load_all((root / "configmaps.yaml").read_text())
               if doc and doc["metadata"]["name"] != "wip-nats-config"]
        (root / "configmaps.yaml").write_text(yaml.safe_dump_all(cms))
        assert not check_nats_authentication(root, d).passed

    def test_not_applicable_without_nats(self, tmp_path: Path, discovery: Discovery) -> None:
        root, d = self._install("compose", tmp_path, discovery, modules=[])
        result = check_nats_authentication(root, d)
        assert result.passed and "not applicable" in result.message


def _app_with_roles(disc: Discovery, roles: list[str]) -> Discovery:
    """A copy of `disc` in which react-console holds these NATS roles."""
    apps = []
    for a in disc.apps:
        if a.metadata.name == "react-console":
            a = a.model_copy(update={"spec": a.spec.model_copy(
                update={"nats_client": NatsClientSpec(roles=roles)}  # type: ignore[arg-type]
            )})
        apps.append(a)
    return Discovery(components=disc.components, apps=apps, errors=disc.errors)


def _with_rc(modules: list[str] | None = None) -> Deployment:
    d = _deployment(modules=modules)
    d.spec.apps = [AppRef(name="react-console")]
    return d


class TestRoles:
    """Apps get NATS access through roles with fixed grants."""

    def test_ingest_producer_publishes_work_items_only(self) -> None:
        publish, deny, subscribe = client_permissions("app", NatsClientSpec(roles=["ingest-producer"]))
        assert publish == [
            "wip.ingest.terminologies.>", "wip.ingest.terms.>",
            "wip.ingest.templates.>", "wip.ingest.documents.>",
        ]
        assert deny == [] and subscribe == ["_INBOX_app.>"]

    def test_event_subscriber_is_confined_to_its_own_durable_on_wip_events(self) -> None:
        publish, _, _ = client_permissions("app", NatsClientSpec(roles=["event-subscriber"]))
        assert publish[0] == "$JS.API.STREAM.INFO.WIP_EVENTS"
        for subject in publish[1:]:
            assert ".WIP_EVENTS.app" in subject, subject
        assert not any(".STREAM.CREATE." in s or ".STREAM.DELETE." in s or ".PURGE." in s for s in publish)

    def test_replay_subscriber_has_no_jetstream_api(self) -> None:
        publish, _, subscribe = client_permissions("app", NatsClientSpec(roles=["replay-subscriber"]))
        assert publish == []
        assert subscribe == ["wip.replay.>", "_INBOX_app.>"]

    def test_nats_monitor_reads_listings_only(self) -> None:
        publish, deny, subscribe = client_permissions("app", NatsClientSpec(roles=["nats-monitor"]))
        assert publish == ["$JS.API.INFO", "$JS.API.STREAM.LIST", "$JS.API.CONSUMER.LIST.*"]
        assert deny == [] and subscribe == ["_INBOX_app.>"]

    def test_app_manifest_with_raw_subjects_is_rejected(self, discovery: Discovery) -> None:
        rc = next(a for a in discovery.apps if a.metadata.name == "react-console")
        data = rc.model_dump()
        data["spec"]["nats_client"] = {"publish": ["$JS.API.STREAM.DELETE.WIP_EVENTS"]}
        with pytest.raises(ValidationError, match="must declare `roles`"):
            type(rc).model_validate(data)

    def test_app_role_becomes_a_user_with_injected_credentials(self, discovery: Discovery) -> None:
        disc = _app_with_roles(discovery, ["ingest-producer", "event-subscriber"])
        d = _with_rc()
        conf = _conf(d, disc)
        assert 'user: "react-console"' in conf
        env = resolve_all_env(d, disc.components, disc.apps, make_spec_context(d, disc.components))
        merged = env["react-console"].merged()
        assert merged["NATS_USER"] == Literal(value="react-console")
        assert merged["NATS_PASSWORD"] == SecretRef(name=password_secret("react-console"))
        assert "NATS_URL" in merged  # injected: the manifest no longer declares it
        assert password_secret("react-console") in collect_required_secrets(d, disc.components, disc.apps)

    @pytest.mark.parametrize("kwargs", [
        {},
        {"roles": ["ingest-producer"], "publish": ["wip.x"]},
        {"roles": ["ingest-producer", "ingest-producer"]},
        {"publish_deny": ["wip.x"]},
    ])
    def test_spec_needs_exactly_one_valid_form(self, kwargs: dict) -> None:
        with pytest.raises(ValidationError):
            NatsClientSpec(**kwargs)
