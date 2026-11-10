"""CASE-1230 item 4 — imports keep what they were given, presets couple the flags they need.

Four defects, each confirmed by both review responses on the case and re-verified
against current HEAD:

1. **`user import` threw away the groups.** `renderers/compose_dex.py` writes a
   `groups:` list on every static password it renders, but the dex-format reader
   hard-coded groups to None. A config exported from one install and imported into
   another therefore lost every membership, and `--groups-file`/`--group` were the
   only way to say it — while the verb's own docstring asserted "a Dex config
   carries no groups", which is false of this deployer's own output.

2. **`--auth-mode api-key-only` alone was refused on a gateway preset.** That mode
   runs no Dex and the gateway exists to authenticate Dex sessions, so
   `AuthSpec.gateway_requires_oidc` refused the spec — yet core, headless and
   `--apps-only` all set both halves together, and only the CLI override set one.
   The mode now implies the pair, and the refusal that remains reachable (an
   explicit gateway) names both flags.

3. **`import-bundle` handed over a URL the next command refuses.** The bundle's
   `external_base_url` was checked only for being a string, then echoed verbatim as
   the copy-pasteable `--remote-wip` argument; `network.remote_wip_url` refuses any
   path, including a bare trailing slash. Both doors now run one rule
   (`base_url_problem`), and the bundle is refused at parse — not tidied in the
   print — because the same value is also recorded in `bootstrap.yaml`.

4. **The analytics preset was described backwards.** "standard + reporting" when
   analytics adds no module that standard lacks and drops Garage (file storage);
   and its remedy hint named `--add files`, which is not a component — `add-module`
   refuses anything that is not a discovered optional component, and the store is
   `garage`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from wip_deploy.build import build_deployment
from wip_deploy.cli import _load_import_entries, app
from wip_deploy.config_gen.dex import DexConfig, DexUserEntry
from wip_deploy.config_gen.env import SecretRef
from wip_deploy.discovery import Discovery, discover
from wip_deploy.import_bundle import BundleParseError, parse_bundle
from wip_deploy.presets import PRESETS
from wip_deploy.renderers.compose_dex import _render_static_passwords
from wip_deploy.secrets import bcrypt_secret_name
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import NetworkSpec

from .test_build import _minimal_compose_inputs
from .test_case_373_parser import _FAKE_PEM
from .test_case_964_supplied_hashes import (
    _dex_config,
    _entry,
    _hash,
    _install,
    _invoke,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "deployer" / "src" / "wip_deploy"
runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


# ──────────────────────────────────────────────────────────────────────
# 1. Groups survive a Dex-config import


def test_dex_groups_reach_the_declared_user(tmp_path: Path) -> None:
    """The whole point: memberships in the file are memberships on the user.

    Before the fix this import exited 2 on "no groups" — the reader had dropped
    the only place the groups were written.
    """
    install = _install(tmp_path)
    config = _dex_config(tmp_path, _entry("alice", groups=["wip-editors", "app-x"]))

    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = _invoke(install, "user", "import", str(config))

    assert result.exit_code == 0, result.output
    users = {u.username: u for u in mutate.call_args.args[0].spec.auth.users}
    assert users["alice"].groups == ["wip-editors", "app-x"]


def test_dex_groups_as_one_comma_separated_string(tmp_path: Path) -> None:
    """A YAML string carries the names the same way a CSV column does."""
    install = _install(tmp_path)
    config = _dex_config(tmp_path, _entry("alice", groups="wip-editors, app-x"))

    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = _invoke(install, "user", "import", str(config))

    assert result.exit_code == 0, result.output
    users = {u.username: u for u in mutate.call_args.args[0].spec.auth.users}
    assert users["alice"].groups == ["wip-editors", "app-x"]


def test_file_carried_groups_outrank_the_groups_file_and_flag(tmp_path: Path) -> None:
    """Precedence is one rule for both formats: file, then --groups-file, then --group."""
    install = _install(tmp_path)
    config = _dex_config(tmp_path, _entry("alice", groups=["from-the-dex-config"]))
    groups = tmp_path / "groups.yaml"
    groups.write_text("alice: [from-the-groups-file]\n")

    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = _invoke(
            install, "user", "import", str(config),
            "--groups-file", str(groups), "--group", "from-the-flag",
        )

    assert result.exit_code == 0, result.output
    users = {u.username: u for u in mutate.call_args.args[0].spec.auth.users}
    assert users["alice"].groups == ["from-the-dex-config"]


def test_an_entry_without_groups_still_falls_back(tmp_path: Path) -> None:
    """Adding the key must not strand a config that carries none."""
    install = _install(tmp_path)
    mixed = _dex_config(
        tmp_path,
        _entry("alice", groups=["wip-editors"]),
        _entry("bob"),
    )

    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = _invoke(install, "user", "import", str(mixed), "--group", "wip-viewers")

    assert result.exit_code == 0, result.output
    users = {u.username: u for u in mutate.call_args.args[0].spec.auth.users}
    assert users["alice"].groups == ["wip-editors"]
    assert users["bob"].groups == ["wip-viewers"]


def test_what_the_deployer_renders_is_what_the_import_reads(tmp_path: Path) -> None:
    """Round trip through the real renderer, not a hand-typed shape.

    This is the claim the defect rests on: a migrating operator feeds this
    deployer's own dex-config back into `user import`.
    """
    entry = DexUserEntry(
        email="alice@example.com",
        username="alice",
        groups=["wip-editors", "app-x"],
        password_secret=SecretRef(name="dex-password-alice"),
        user_id="alice-001",
        password_supplied=True,
    )
    dex = DexConfig(
        issuer="https://wip.localhost:8443/dex",
        users=[entry],
        clients=[],
        id_token_ttl="15m",
        connectors=[],
    )
    secrets = ResolvedSecrets(values={bcrypt_secret_name("dex-password-alice"): _hash()})

    rendered = _render_static_passwords(dex, secrets)
    assert rendered[0]["groups"] == ["wip-editors", "app-x"], "renderer emits groups"

    config = tmp_path / "rendered-dex.yaml"
    config.write_text(yaml.safe_dump({"staticPasswords": rendered}))
    (read_back,) = _load_import_entries(config, "dex")

    assert read_back["groups"] == ["wip-editors", "app-x"]
    assert read_back["username"] == "alice"
    assert read_back["user_id"] == "alice-001"


# ──────────────────────────────────────────────────────────────────────
# 2. --auth-mode api-key-only brings the gateway with it


def test_api_key_only_alone_clears_the_gateway_on_a_gateway_preset() -> None:
    d = build_deployment(_minimal_compose_inputs(auth_mode="api-key-only"))
    assert d.spec.auth.mode == "api-key-only"
    assert d.spec.auth.gateway is False
    # No Dex means no Dex users to declare — the shape --apps-only produces.
    assert d.spec.auth.users == []


@pytest.mark.parametrize("preset", ["standard", "analytics", "full"])
def test_every_gateway_preset_accepts_the_mode_alone(preset: str) -> None:
    """All three ship `gateway: True`, so all three needed the coupling."""
    assert PRESETS[preset]["auth"]["gateway"] is True
    d = build_deployment(_minimal_compose_inputs(preset=preset, auth_mode="api-key-only"))
    assert d.spec.auth.gateway is False


def test_an_explicit_gateway_is_still_a_conflict_and_names_the_flag() -> None:
    """Inference never overrides an explicit instruction; the contradiction then
    has to explain itself, which it previously did not."""
    with pytest.raises(ValidationError) as exc:
        build_deployment(
            _minimal_compose_inputs(auth_mode="api-key-only", auth_gateway=True)
        )
    message = str(exc.value)
    assert "--no-auth-gateway" in message
    assert "--auth-mode" in message


@pytest.mark.parametrize("mode", ["oidc", "hybrid"])
def test_a_gateway_mode_leaves_the_preset_gateway_alone(mode: str) -> None:
    d = build_deployment(_minimal_compose_inputs(auth_mode=mode))  # type: ignore[arg-type]
    assert d.spec.auth.gateway is True


@pytest.mark.parametrize("preset", ["core", "headless"])
def test_a_preset_already_in_that_shape_is_unchanged(preset: str) -> None:
    """The coupling must not rewrite what the preset already declared — including
    keeping the default Dex users it deliberately ships as `[]`."""
    d = build_deployment(_minimal_compose_inputs(preset=preset))
    assert d.spec.auth.mode == "api-key-only"
    assert d.spec.auth.gateway is False
    assert d.spec.auth.users == []


def test_the_coupled_spec_matches_the_documented_pair() -> None:
    """`--auth-mode api-key-only` alone and the explicit pair must land on the
    same spec — otherwise the shorthand and the longhand are two installs, and the
    drift guard would read one as the other."""
    shorthand = build_deployment(_minimal_compose_inputs(auth_mode="api-key-only"))
    explicit = build_deployment(
        _minimal_compose_inputs(auth_mode="api-key-only", auth_gateway=False)
    )
    assert shorthand.spec.auth == explicit.spec.auth


# ──────────────────────────────────────────────────────────────────────
# 3. A bundle URL is checked by the rule it will be validated against


def _bundle_yaml(external_base_url: str) -> str:
    from datetime import UTC, datetime, timedelta

    body = {
        "api_version": "wip.dev/v1",
        "kind": "BootstrapBundle",
        "metadata": {
            "name": "test-bundle",
            "generated_at": datetime.now(UTC).isoformat(),
        },
        "spec": {
            "external_base_url": external_base_url,
            "api_key": {
                "value": "opaque-secret-value",
                "name": "test-key",
                "scope": {"namespaces": ["kb"], "permissions": "read"},
                "expires_at": (datetime.now(UTC) + timedelta(days=365)).isoformat(),
            },
            "ca_cert": _FAKE_PEM,
            "suggested_apps": [{"name": "react-console"}],
        },
    }
    return yaml.safe_dump(body, sort_keys=False)


@pytest.mark.parametrize("url", ["https://wip.example", "http://wip.example:8443"])
def test_a_bare_origin_is_accepted(url: str) -> None:
    assert parse_bundle(_bundle_yaml(url)).external_base_url == url


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("https://wip.example/", "path"),
        ("https://wip.example/dex", "path"),
        ("wip.example", "scheme"),
        ("ftp://wip.example", "scheme"),
        ("https://", "hostname"),
        ("https://wip.example?foo=bar", "query or fragment"),
        ("https://wip.example#frag", "query or fragment"),
    ],
)
def test_both_doors_refuse_the_same_shapes(url: str, reason: str) -> None:
    """The bundle door and the spec door must not disagree about one URL.

    Before this, a bundle could carry `https://host/` through import and print it
    as the `--remote-wip` argument the install then rejected.
    """
    with pytest.raises(BundleParseError, match=reason) as exc:
        parse_bundle(_bundle_yaml(url))
    assert exc.value.path == "spec.external_base_url"

    with pytest.raises(ValidationError, match=reason):
        NetworkSpec(hostname="wip.localhost", remote_wip_url=url)


def test_import_bundle_refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal lands before any secret is created — nothing to roll back."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    bundle_file = tmp_path / "bundle.yaml"
    bundle_file.write_text(_bundle_yaml("https://wip.example/"))
    result = runner.invoke(app, ["import-bundle", str(bundle_file), "--name", "laptop-rc"])

    assert result.exit_code == 2
    assert "bundle invalid" in result.output
    assert "spec.external_base_url" in result.output
    assert not (fake_home / ".wip-deploy" / "laptop-rc").exists()


# ──────────────────────────────────────────────────────────────────────
# 4. What the presets are actually made of


def test_analytics_is_standard_without_the_object_store() -> None:
    """It adds no module standard lacks, and drops exactly Garage."""
    standard = set(PRESETS["standard"]["modules"]["optional"])
    analytics = set(PRESETS["analytics"]["modules"]["optional"])
    assert analytics < standard
    assert standard - analytics == {"garage"}


def test_full_adds_the_store_and_ingest_back_onto_analytics() -> None:
    analytics = set(PRESETS["analytics"]["modules"]["optional"])
    full = set(PRESETS["full"]["modules"]["optional"])
    assert full - analytics == {"garage", "ingest-gateway"}


def test_the_remedy_names_a_module_add_module_would_accept(discovery: Discovery) -> None:
    """`--add files` was advice for a command that refuses it: the store is
    `garage`, and `add-module` takes only a discovered optional component."""
    optional = {c.metadata.name for c in discovery.components if c.metadata.category == "optional"}
    assert "garage" in optional
    assert "files" not in optional


def test_the_examples_output_describes_analytics_by_its_real_diff() -> None:
    result = runner.invoke(app, ["examples"])
    assert result.exit_code == 0
    line = next(
        line for line in result.output.splitlines() if line.strip().startswith("analytics")
    )
    assert "minus" in line and "garage" in line.lower()
    assert "+ reporting" not in line


@pytest.mark.parametrize(
    ("source", "stale"),
    [
        ("cli.py", "standard + reporting"),
        ("presets/analytics.py", "Standard + reporting"),
        ("presets/analytics.py", "--add files"),
    ],
)
def test_a_claim_the_preset_data_refutes_is_not_left_in_the_text(
    source: str, stale: str
) -> None:
    """Pinned as absence so the wording cannot drift back silently: the preset
    lists above are the ground truth these sentences contradict."""
    assert stale not in (SRC / source).read_text(encoding="utf-8")


def test_no_shipped_text_advises_the_invalid_gateway_form() -> None:
    """`--auth-gateway` is a boolean pair (`--auth-gateway/--no-auth-gateway`), so
    `--auth-gateway false` is refused at the parser — advice that could not be
    followed."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in SRC.rglob("*.py")
        if "--auth-gateway true" in path.read_text(encoding="utf-8")
        or "--auth-gateway false" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
