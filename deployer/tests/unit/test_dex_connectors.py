"""Dex connectors and federated group mappings.

External identity providers are spec-declared (auth.connectors) and
rendered into Dex's config on every target; the WIP groups their users
get are spec-declared too (auth.group_mappings) and reach every
token-validating service and the gateway as WIP_AUTH_FEDERATED_GROUPS.

Contracts under test:
- the deployer owns a connector's redirectURI (derived from the issuer)
  and its secret (from the secret backend, written into the config);
- a connector secret is operator-supplied: never generated, and a missing
  one stops the apply with instructions;
- wip-admins and wip-services are never granted to a federated login;
- the verbs persist exactly that, and keep secrets off the command line.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.config_gen.spec_context import make_spec_context
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.compose_dex import dex_config_hash, render_dex_conf
from wip_deploy.secrets import OperatorSecretMissing, collect_required_secrets, ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend, ResolvedSecrets
from wip_deploy.spec import (
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    DexConnector,
    GroupMapping,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()

GOOGLE = DexConnector(
    id="google", type="oidc", name="Google",
    config={"issuer": "https://accounts.google.com", "clientID": "abc.apps"},
)
LDAP = DexConnector(
    id="corp", type="ldap", name="Corp LDAP",
    config={"host": "ldap.corp:636"}, secret_key="bindPW",
)


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(connectors=(), mappings=(), mode: str = "oidc") -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(
                mode=mode, gateway=True,  # type: ignore[arg-type]
                connectors=list(connectors), group_mappings=list(mappings),
            ),
            network=NetworkSpec(hostname="wip.example.org"),
            platform=PlatformSpec(compose=ComposePlatform(data_dir="/tmp/d")),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
        ),
    )


def _secrets(d: Deployment, discovery: Discovery, **values: str) -> ResolvedSecrets:
    names = collect_required_secrets(d, discovery.components, discovery.apps)
    return ResolvedSecrets(values={n: values.get(n, "x") for n in names})


def _rendered_connectors(d: Deployment, discovery: Discovery, **values: str) -> list[dict]:
    conf = render_dex_conf(d, discovery.components, discovery.apps, _secrets(d, discovery, **values))
    assert conf is not None
    return yaml.safe_load(conf)["connectors"]


class TestSpec:
    def test_local_is_dex_own_connector(self) -> None:
        with pytest.raises(ValidationError, match="local-user connector"):
            DexConnector(id="local", type="oidc", name="x")

    @pytest.mark.parametrize("key", ["redirectURI", "clientSecret"])
    def test_deployer_owned_keys_refused(self, key: str) -> None:
        with pytest.raises(ValidationError, match="may not set"):
            DexConnector(id="g", type="oidc", name="x", config={key: "v"})

    @pytest.mark.parametrize("type_", ["oidc", "atlassian-crowd", "mockCallback"])
    def test_dex_type_names_accepted(self, type_: str) -> None:
        assert DexConnector(id="x", type=type_, name="X", secret_key=None).type == type_

    def test_duplicate_connector_ids_refused(self) -> None:
        with pytest.raises(ValidationError, match="duplicate connector ids"):
            _deployment([GOOGLE, GOOGLE])

    def test_connectors_need_dex(self) -> None:
        with pytest.raises(ValidationError, match="api-key-only"):
            AuthSpec(mode="api-key-only", gateway=False, users=[], connectors=[GOOGLE])

    def test_mappings_need_a_connector(self) -> None:
        with pytest.raises(ValidationError, match="declare a connector first"):
            _deployment(mappings=[GroupMapping(email_domain="corp.com", group="wip-editors")])

    @pytest.mark.parametrize("group", ["wip-admins", "wip-services"])
    def test_reserved_groups_never_mapped(self, group: str) -> None:
        with pytest.raises(ValidationError, match="reserved for local users"):
            GroupMapping(email_domain="corp.com", group=group)

    def test_mapping_matches_exactly_one_way(self) -> None:
        with pytest.raises(ValidationError, match="exactly one"):
            GroupMapping(email="a@corp.com", email_domain="corp.com", group="g")
        with pytest.raises(ValidationError, match="exactly one"):
            GroupMapping(group="g")


class TestRender:
    def test_no_connectors_renders_an_empty_list(self, discovery: Discovery) -> None:
        assert _rendered_connectors(_deployment(), discovery) == []

    def test_oauth_connector_gets_derived_redirect_and_inlined_secret(
        self, discovery: Discovery
    ) -> None:
        (c,) = _rendered_connectors(
            _deployment([GOOGLE]), discovery, **{"dex-connector-google": "s3cret"}
        )
        assert c["id"] == "google" and c["type"] == "oidc" and c["name"] == "Google"
        assert c["config"] == {
            "issuer": "https://accounts.google.com",
            "clientID": "abc.apps",
            "redirectURI": "https://wip.example.org:8443/dex/callback",
            "clientSecret": "s3cret",
        }

    def test_password_connector_gets_no_redirect_and_its_own_secret_key(
        self, discovery: Discovery
    ) -> None:
        (c,) = _rendered_connectors(_deployment([LDAP]), discovery, **{"dex-connector-corp": "pw"})
        assert "redirectURI" not in c["config"]
        assert c["config"]["bindPW"] == "pw"

    def test_connector_without_a_secret(self, discovery: Discovery) -> None:
        saml = DexConnector(id="idp", type="saml", name="IdP", config={"ssoURL": "u"}, secret_key=None)
        d = _deployment([saml])
        assert "dex-connector-idp" not in collect_required_secrets(d, discovery.components, discovery.apps)
        (c,) = _rendered_connectors(d, discovery)
        assert set(c["config"]) == {"ssoURL", "redirectURI"}

    def test_secret_rotation_changes_the_dex_config_hash(self, discovery: Discovery) -> None:
        d = _deployment([GOOGLE])

        def digest(secret: str) -> str:
            conf = render_dex_conf(
                d, discovery.components, discovery.apps,
                _secrets(d, discovery, **{"dex-connector-google": secret}),
            )
            assert conf is not None
            return dex_config_hash(conf)

        assert digest("one") == digest("one")
        assert digest("one") != digest("two")


class TestOperatorSuppliedSecret:
    def test_collected(self, discovery: Discovery) -> None:
        names = collect_required_secrets(_deployment([GOOGLE]), discovery.components, discovery.apps)
        assert "dex-connector-google" in names

    def test_missing_secret_stops_the_apply_and_is_not_invented(
        self, discovery: Discovery, tmp_path: Path
    ) -> None:
        backend = FileSecretBackend(tmp_path)
        with pytest.raises(OperatorSecretMissing, match="connector add"):
            ensure_secrets(_deployment([GOOGLE]), discovery.components, discovery.apps, backend)
        assert not (tmp_path / "dex-connector-google").exists()

    def test_supplied_secret_is_used_as_is(self, discovery: Discovery, tmp_path: Path) -> None:
        (tmp_path / "dex-connector-google").write_text("from-google")
        resolved = ensure_secrets(
            _deployment([GOOGLE]), discovery.components, discovery.apps, FileSecretBackend(tmp_path)
        )
        assert resolved.get("dex-connector-google") == "from-google"


class TestFederatedGroupsPolicy:
    def test_empty_without_connectors(self, discovery: Discovery) -> None:
        ctx = make_spec_context(_deployment(), discovery.components, discovery.apps)
        assert ctx.auth.federated_groups == ""

    def test_policy_lists_reserved_groups_and_mappings(self, discovery: Discovery) -> None:
        d = _deployment(
            [GOOGLE],
            [GroupMapping(email_domain="corp.com", group="wip-editors"),
             GroupMapping(email="ana@else.org", group="wip-viewers")],
        )
        policy = json.loads(make_spec_context(d, discovery.components, discovery.apps).auth.federated_groups)
        assert policy == {
            "local_connectors": ["local"],
            "reserved_groups": ["wip-admins", "wip-services"],
            "mappings": [
                {"email_domain": "corp.com", "group": "wip-editors"},
                {"email": "ana@else.org", "group": "wip-viewers"},
            ],
        }

    def test_every_token_validating_service_and_the_gateway_receive_it(
        self, discovery: Discovery
    ) -> None:
        receivers = {
            c.metadata.name
            for c in discovery.components
            for section in (c.spec.env.required, c.spec.env.optional)
            for e in section
            if e.name == "WIP_AUTH_FEDERATED_GROUPS"
        }
        validators = {
            c.metadata.name
            for c in discovery.components
            for section in (c.spec.env.required, c.spec.env.optional)
            for e in section
            if e.name == "WIP_AUTH_JWT_ISSUER_URL"
        }
        assert validators
        assert receivers == validators | {"auth-gateway"}


def _install(tmp_path: Path) -> Path:
    d = build_deployment(
        BuildInputs(target="compose", variant="dev", hostname="localhost", tls="internal",
                    compose_data_dir=tmp_path / "data")
    )
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    return tmp_path


def _invoke(install: Path, *args: str, input: str | None = None):
    return runner.invoke(
        app, [*args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT)], input=input
    )


class TestVerbs:
    def test_connector_add_stores_the_secret_and_declares_the_connector(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        secret = tmp_path / "google-secret"
        secret.write_text("from-google\n")
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install, "connector", "add", "google", "--type", "oidc",
                "--display-name", "Google", "--config", "issuer=https://accounts.google.com",
                "--config", "clientID=abc.apps", "--config", "insecureSkipEmailVerified=true",
                "--secret-file", str(secret),
            )
        assert result.exit_code == 0, result.output
        (connector,) = mutate.call_args.args[0].spec.auth.connectors
        assert connector.config == {
            "issuer": "https://accounts.google.com", "clientID": "abc.apps",
            "insecureSkipEmailVerified": True,
        }
        assert (tmp_path / "secrets" / "dex-connector-google").read_text().strip() == "from-google"
        assert "/dex/callback" in result.output

    def test_connector_add_reads_the_secret_from_stdin(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        with patch("wip_deploy.cli._apply_and_persist_mutation"):
            result = _invoke(
                install, "connector", "add", "gh", "--type", "github", "--display-name", "GitHub",
                "--config", "clientID=id", "--secret-file", "-", input="piped\n",
            )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "secrets" / "dex-connector-gh").read_text().strip() == "piped"

    def test_connector_add_without_a_secret_is_refused(self, tmp_path: Path) -> None:
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                _install(tmp_path), "connector", "add", "google", "--type", "oidc",
                "--display-name", "Google",
            )
        assert result.exit_code == 2
        assert "--secret-file is required" in result.output
        mutate.assert_not_called()

    def test_secret_in_config_is_refused(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path), "connector", "add", "google", "--type", "oidc",
            "--display-name", "Google", "--config", "clientSecret=leak",
        )
        assert result.exit_code == 2
        assert "may not set" in result.output

    def test_group_map_refuses_wip_admins(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path), "group-map", "add", "--email-domain", "corp.com",
            "--group", "wip-admins",
        )
        assert result.exit_code == 2
        assert "reserved for local users" in result.output

    def test_group_map_needs_a_connector(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path), "group-map", "add", "--email-domain", "corp.com",
            "--group", "wip-editors",
        )
        assert result.exit_code == 2
        assert "declare a connector first" in result.output
