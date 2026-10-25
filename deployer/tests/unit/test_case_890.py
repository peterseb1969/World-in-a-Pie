"""CASE-890 — MongoDB requires authentication; each client is its own user.

MongoDB accepted anonymous clients on every target: any workload on the
network could read and write every service's database. Components and apps
now declare a `mongo_client` block; the deployer generates a password per
client plus root, injects the credentials, and renders a start script that
converges root, the users and the custom roles through a temporary mongod
without --auth on loopback, then serves with --auth.

Contract under test: the desired users and roles match the declarations
(registry's cross-database access is find/remove on named collections only,
apps are read-only); the credentials reach both sides; the script holds no
secret value; every target mounts the script and a changed user set
restarts MongoDB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from wip_deploy.config_gen import make_spec_context, resolve_all_env
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY
from wip_deploy.config_gen.env import Literal, SecretRef
from wip_deploy.config_gen.mongodb import (
    ENTRYPOINT_RENDER_PATH,
    ROOT_PASSWORD_SECRET,
    desired_state,
    password_secret,
    render_mongodb_entrypoint,
    server_password_env,
)
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_dev_simple, render_k8s
from wip_deploy.secrets import _format_generator, collect_required_secrets, ensure_secrets
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
    MongoClientSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)
from wip_deploy.verify_security import check_mongodb_authentication

REPO_ROOT = Path(__file__).resolve().parents[3]
_ALL_DBS = ["wip_def_store", "wip_document_store", "wip_registry", "wip_template_store"]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str = "compose", apps: list[str] | None = None) -> Deployment:
    platform = {
        "compose": PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
        "dev": PlatformSpec(dev=DevPlatform(mode="simple", apps_from_registry=apps or [])),
        "k8s": PlatformSpec(k8s=K8sPlatform(namespace="wip-test")),
    }[target]
    return Deployment(
        metadata=DeploymentMetadata(name="case890"),
        spec=DeploymentSpec(
            target=target,  # type: ignore[arg-type]
            modules={"optional": ["reporting-sync"]},  # type: ignore[arg-type]
            apps=[AppRef(name=n) for n in (apps or [])],
            auth=AuthSpec(mode="oidc", gateway=True),
            network=NetworkSpec(hostname="wip.local"),
            images=ImagesSpec(registry="ghcr.io/peterseb1969", tag="v2.0.0"),
            platform=platform,
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _users(d: Deployment, disc: Discovery) -> dict[str, list[dict[str, str]]]:
    return {u["user"]: u["roles"] for u in desired_state(d, disc.components, disc.apps)["users"]}


class TestDesiredState:
    def test_services_get_their_own_database(self, discovery: Discovery) -> None:
        users = _users(_deployment(), discovery)
        assert users["def-store"] == [{"role": "readWrite", "db": "wip_def_store"}]
        assert users["template-store"] == [{"role": "readWrite", "db": "wip_template_store"}]

    def test_document_store_reads_and_writes_all_four_for_restore(self, discovery: Discovery) -> None:
        roles = _users(_deployment(), discovery)["document-store"]
        assert sorted(r["db"] for r in roles) == _ALL_DBS
        assert {r["role"] for r in roles} == {"readWrite"}

    def test_registry_reaches_other_databases_only_to_find_and_remove(self, discovery: Discovery) -> None:
        state = desired_state(_deployment(), discovery.components, discovery.apps)
        roles = {u["user"]: u["roles"] for u in state["users"]}["registry"]
        assert {"role": "readWrite", "db": "wip_registry"} in roles
        custom = [r for r in state["roles"] if r["role"] == "wip-registry-collections"]
        assert len(custom) == 1 and len(custom[0]["privileges"]) == 8
        for p in custom[0]["privileges"]:
            assert p["actions"] == ["find", "remove"]
            assert p["resource"]["db"] != "wip_registry"
        assert {"role": "wip-registry-collections", "db": "admin"} in roles

    def test_root_is_always_converged(self, discovery: Discovery) -> None:
        state = desired_state(_deployment(), discovery.components, discovery.apps)
        root = next(u for u in state["users"] if u["user"] == "root")
        assert root["roles"] == [{"role": "root", "db": "admin"}]
        assert root["passwordEnv"] == "MONGO_ROOT_PASSWORD"

    def test_react_console_reads_every_database_and_writes_none(self, discovery: Discovery) -> None:
        roles = _users(_deployment(apps=["react-console"]), discovery)["react-console"]
        assert sorted(r["db"] for r in roles) == _ALL_DBS
        assert {r["role"] for r in roles} == {"read"}

    def test_disabled_app_has_no_user(self, discovery: Discovery) -> None:
        assert "react-console" not in _users(_deployment(), discovery)


class TestSpec:
    def test_app_cannot_declare_databases(self, discovery: Discovery) -> None:
        rc = next(a for a in discovery.apps if a.metadata.name == "react-console")
        data = rc.model_dump()
        data["spec"]["mongo_client"] = {"databases": ["wip_registry"]}
        with pytest.raises(ValidationError, match="apps never write to MongoDB"):
            type(rc).model_validate(data)

    @pytest.mark.parametrize("kwargs", [{}, {"roles": ["read-all"], "databases": ["x"]}])
    def test_exactly_one_form(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            MongoClientSpec(**kwargs)


class TestCredentials:
    def test_secrets_collected_with_plain_alphabet(self, discovery: Discovery) -> None:
        names = collect_required_secrets(_deployment(apps=["react-console"]), discovery.components, discovery.apps)
        expected = {ROOT_PASSWORD_SECRET, *(password_secret(c) for c in
                    ("def-store", "document-store", "registry", "template-store", "react-console"))}
        assert expected <= names
        for name in expected:
            value = _format_generator(name)()
            assert value[0].isalpha() and value.isalnum() and len(value) == 22

    def test_clients_and_server_receive_the_credentials(self, discovery: Discovery) -> None:
        d = _deployment(apps=["react-console"])
        env = resolve_all_env(d, discovery.components, discovery.apps, make_spec_context(d, discovery.components))
        for client in ("def-store", "document-store", "registry", "template-store", "react-console"):
            merged = env[client].merged()
            assert merged["MONGO_USER"] == Literal(value=client)
            assert merged["MONGO_PASSWORD"] == SecretRef(name=password_secret(client))
            assert env["mongodb"].merged()[server_password_env(client)] == SecretRef(name=password_secret(client))
        assert env["mongodb"].merged()["MONGO_ROOT_PASSWORD"] == SecretRef(name=ROOT_PASSWORD_SECRET)
        assert not any(k.startswith("MONGO_INITDB") for k in env["mongodb"].merged())


class TestScript:
    def test_converges_on_loopback_then_serves_with_auth(self, discovery: Discovery) -> None:
        script = render_mongodb_entrypoint(_deployment(), discovery.components, discovery.apps)
        assert script is not None
        assert "mongod --bind_ip 127.0.0.1 --port 27018" in script
        assert "exec mongod --auth --bind_ip_all" in script
        assert 'exec gosu mongodb bash "$BASH_SOURCE" "$@"' in script

    def test_holds_no_secret_value(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _deployment(apps=["react-console"])
        secrets = ensure_secrets(d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"))
        script = render_mongodb_entrypoint(d, discovery.components, discovery.apps)
        assert script is not None
        for value in secrets.values.values():
            assert value not in script

    def test_deterministic(self, discovery: Discovery) -> None:
        a = render_mongodb_entrypoint(_deployment(), discovery.components, discovery.apps)
        assert a == render_mongodb_entrypoint(_deployment(), discovery.components, discovery.apps)


def _render(target: str, tmp_path: Path, disc: Discovery, apps: list[str] | None = None) -> tuple[Any, ResolvedSecrets, Deployment]:
    d = _deployment(target, apps)
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(tmp_path / "s"))
    tree = {
        "compose": lambda: render_compose(d, disc.components, disc.apps, s),
        "dev": lambda: render_dev_simple(d, disc.components, disc.apps, s, repo_root=REPO_ROOT),
        "k8s": lambda: render_k8s(d, disc.components, disc.apps, s),
    }[target]()
    return tree, s, d


@pytest.mark.parametrize("target", ["compose", "dev"])
def test_compose_targets_mount_and_label(target: str, tmp_path: Path, discovery: Discovery) -> None:
    tree, _, _ = _render(target, tmp_path, discovery)
    assert tree.files[Path(ENTRYPOINT_RENDER_PATH)].mode == 0o644
    svc = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]["mongodb"]
    assert f"./{ENTRYPOINT_RENDER_PATH}:/etc/wip-mongo/entrypoint.sh:ro" in svc["volumes"]
    assert svc["entrypoint"] == ["bash"]
    assert svc["command"][0] == "/etc/wip-mongo/entrypoint.sh"
    before = svc["labels"][CONFIG_HASH_KEY]
    tree2, _, _ = _render(target, tmp_path, discovery, apps=["react-console"])
    after = yaml.safe_load(tree2.files[Path("docker-compose.yaml")].content)["services"]["mongodb"]["labels"][CONFIG_HASH_KEY]
    assert before != after  # adding an app's user restarts MongoDB


def test_k8s_configmap_mount_and_annotation(tmp_path: Path, discovery: Discovery) -> None:
    tree, _, d = _render("k8s", tmp_path, discovery)
    docs = [doc for e in tree.files.values() for doc in yaml.safe_load_all(e.content) if doc]
    cm = next(x for x in docs if x["kind"] == "ConfigMap" and x["metadata"]["name"] == "wip-mongodb-entrypoint")
    assert cm["data"]["entrypoint.sh"] == render_mongodb_entrypoint(d, discovery.components, discovery.apps)
    sts = next(x for x in docs if x["kind"] == "StatefulSet" and x["metadata"]["name"] == "wip-mongodb")
    pod = sts["spec"]["template"]
    assert CONFIG_HASH_KEY in pod["metadata"]["annotations"]
    assert {"name": "config", "configMap": {"name": "wip-mongodb-entrypoint"}} in pod["spec"]["volumes"]


class TestVerifySecurity:
    @pytest.mark.parametrize("target", ["compose", "dev", "k8s"])
    def test_fresh_render_passes(self, target: str, tmp_path: Path, discovery: Discovery) -> None:
        tree, _, d = _render(target, tmp_path, discovery)
        tree.write(tmp_path / "install")
        result = check_mongodb_authentication(tmp_path / "install", d)
        assert result.passed, result.message
        assert "5 MongoDB users" in result.message  # root + four services

    def test_script_without_auth_fails(self, tmp_path: Path, discovery: Discovery) -> None:
        tree, _, d = _render("compose", tmp_path, discovery)
        tree.write(tmp_path / "install")
        (tmp_path / "install" / ENTRYPOINT_RENDER_PATH).write_text("exec mongod --bind_ip_all\n")
        assert not check_mongodb_authentication(tmp_path / "install", d).passed
