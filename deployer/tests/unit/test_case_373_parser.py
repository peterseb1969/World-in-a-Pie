"""Schema parser tests for CASE-373 Phase 1 — bootstrap bundle import.

Covers `import_bundle.parse_bundle()`. CLI wiring lives in a sibling
test file. The parser is pure (no I/O outside the optional Path-as-
source convenience), so each test pins a YAML payload and asserts
either a `Bundle` or a `BundleParseError` with the expected `path`.

Rejection paths under test:

  - Malformed YAML
  - Wrong api_version / kind
  - Missing required fields (metadata.name, spec.api_key, ca_cert, etc.)
  - Empty namespaces list
  - Missing `permissions` field (no implicit fallback — FR-YAC caveat #1)
  - Unrecognised `permissions` value
  - Expired bundles (expires_at in the past)
  - Bogus PEM (missing BEGIN/END markers)
  - Junk types (suggested_apps not a list, etc.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wip_deploy.import_bundle import Bundle, BundleParseError, parse_bundle

# ──────────────────────────────────────────────────────────────────────
# Fixtures


_FAKE_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCCATigAwIBAgIQ...fake-test-cert...\n"
    "-----END CERTIFICATE-----\n"
)


def _future(days: int = 365) -> str:
    """ISO-formatted UTC timestamp `days` days in the future."""
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _past(days: int = 1) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _good_bundle_yaml(**overrides: object) -> str:
    """Render a minimal valid bundle YAML, with field-level overrides
    for negative-path tests. Each override is a {dotted.path: value}.
    Use the sentinel `_DELETE` to remove a key entirely.
    """
    body: dict = {
        "api_version": "wip.dev/v1",
        "kind": "BootstrapBundle",
        "metadata": {
            "name": "test-bundle",
            "generated_at": _future(0),
        },
        "spec": {
            "external_base_url": "https://wip.example/",
            "api_key": {
                "value": "opaque-key-value",
                "name": "test-key",
                "scope": {
                    "namespaces": ["kb"],
                    "permissions": "read",
                },
                "expires_at": _future(365),
            },
            "ca_cert": _FAKE_PEM,
            "suggested_apps": [{"name": "react-console"}],
        },
    }
    for dotted, val in overrides.items():
        _apply_override(body, dotted.split("."), val)
    return _to_yaml(body)


_DELETE = object()


def _apply_override(node: dict, path: list[str], value: object) -> None:
    head, *rest = path
    if not rest:
        if value is _DELETE:
            node.pop(head, None)
        else:
            node[head] = value
        return
    _apply_override(node[head], rest, value)


def _to_yaml(d: dict) -> str:
    import yaml
    return yaml.safe_dump(d, sort_keys=False)


# ──────────────────────────────────────────────────────────────────────
# Happy path


def test_parse_valid_bundle_returns_structured_data() -> None:
    bundle = parse_bundle(_good_bundle_yaml())

    assert isinstance(bundle, Bundle)
    assert bundle.name == "test-bundle"
    assert bundle.external_base_url == "https://wip.example/"
    assert bundle.api_key.value == "opaque-key-value"
    assert bundle.api_key.name == "test-key"
    assert bundle.api_key.scope.namespaces == ("kb",)
    assert bundle.api_key.scope.permissions == "read"
    assert bundle.suggested_apps == ("react-console",)
    assert bundle.ca_cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert bundle.ca_cert_pem.endswith(b"\n")


def test_parse_bundle_from_bytes() -> None:
    bundle = parse_bundle(_good_bundle_yaml().encode())
    assert bundle.name == "test-bundle"


def test_parse_bundle_from_path(tmp_path: Path) -> None:
    p = tmp_path / "bundle.yaml"
    p.write_text(_good_bundle_yaml())
    bundle = parse_bundle(p)
    assert bundle.name == "test-bundle"


def test_parse_bundle_path_not_found(tmp_path: Path) -> None:
    with pytest.raises(BundleParseError, match="not found"):
        parse_bundle(tmp_path / "does-not-exist.yaml")


# ──────────────────────────────────────────────────────────────────────
# Schema-level rejection


def test_rejects_malformed_yaml() -> None:
    with pytest.raises(BundleParseError, match="malformed"):
        parse_bundle("{this is not: valid: yaml: at: all")


def test_rejects_non_mapping_top_level() -> None:
    with pytest.raises(BundleParseError, match="mapping at the top level"):
        parse_bundle("- just\n- a\n- list\n")


def test_rejects_wrong_api_version() -> None:
    with pytest.raises(BundleParseError, match="api_version"):
        parse_bundle(_good_bundle_yaml(api_version="wip.dev/v2"))


def test_rejects_wrong_kind() -> None:
    with pytest.raises(BundleParseError, match="kind"):
        parse_bundle(_good_bundle_yaml(kind="WrongKind"))


# ──────────────────────────────────────────────────────────────────────
# Missing-field rejection


def test_rejects_missing_metadata_name() -> None:
    with pytest.raises(BundleParseError) as exc:
        parse_bundle(_good_bundle_yaml(**{"metadata.name": _DELETE}))
    assert exc.value.path == "metadata.name"


def test_rejects_missing_external_base_url() -> None:
    with pytest.raises(BundleParseError) as exc:
        parse_bundle(_good_bundle_yaml(**{"spec.external_base_url": _DELETE}))
    assert exc.value.path == "spec.external_base_url"


def test_rejects_missing_api_key_value() -> None:
    with pytest.raises(BundleParseError) as exc:
        parse_bundle(_good_bundle_yaml(**{"spec.api_key.value": _DELETE}))
    assert exc.value.path == "spec.api_key.value"


def test_rejects_missing_ca_cert() -> None:
    with pytest.raises(BundleParseError) as exc:
        parse_bundle(_good_bundle_yaml(**{"spec.ca_cert": _DELETE}))
    assert exc.value.path == "spec.ca_cert"


# ──────────────────────────────────────────────────────────────────────
# Least-privilege enforcement (FR-YAC caveat #1)


def test_rejects_missing_permissions_no_implicit_write() -> None:
    """The whole point of CASE-373's least-privilege default: if the
    operator omits `permissions`, the bundle must not silently grant
    write. The parser refuses to guess."""
    with pytest.raises(BundleParseError, match="permissions") as exc:
        parse_bundle(
            _good_bundle_yaml(**{"spec.api_key.scope.permissions": _DELETE})
        )
    assert exc.value.path == "spec.api_key.scope.permissions"


def test_rejects_unrecognised_permissions() -> None:
    with pytest.raises(BundleParseError, match="permissions"):
        parse_bundle(_good_bundle_yaml(**{"spec.api_key.scope.permissions": "admin"}))


def test_accepts_read_permissions() -> None:
    bundle = parse_bundle(_good_bundle_yaml(**{"spec.api_key.scope.permissions": "read"}))
    assert bundle.api_key.scope.permissions == "read"


def test_accepts_write_permissions() -> None:
    bundle = parse_bundle(_good_bundle_yaml(**{"spec.api_key.scope.permissions": "write"}))
    assert bundle.api_key.scope.permissions == "write"


# ──────────────────────────────────────────────────────────────────────
# Namespaces


def test_rejects_empty_namespaces_list() -> None:
    with pytest.raises(BundleParseError, match="namespaces"):
        parse_bundle(_good_bundle_yaml(**{"spec.api_key.scope.namespaces": []}))


def test_accepts_multiple_namespaces() -> None:
    bundle = parse_bundle(
        _good_bundle_yaml(**{"spec.api_key.scope.namespaces": ["kb", "wip"]})
    )
    assert bundle.api_key.scope.namespaces == ("kb", "wip")


# ──────────────────────────────────────────────────────────────────────
# Expiry


def test_rejects_expired_bundle() -> None:
    """Catches the 'I scp'd this two years ago' footgun before any
    secret hits disk."""
    with pytest.raises(BundleParseError, match="expired") as exc:
        parse_bundle(_good_bundle_yaml(**{"spec.api_key.expires_at": _past(1)}))
    assert exc.value.path == "spec.api_key.expires_at"


def test_rejects_unparseable_expires_at() -> None:
    with pytest.raises(BundleParseError, match="ISO"):
        parse_bundle(_good_bundle_yaml(**{"spec.api_key.expires_at": "not a date"}))


# ──────────────────────────────────────────────────────────────────────
# CA cert


def test_rejects_bogus_ca_cert() -> None:
    with pytest.raises(BundleParseError, match="PEM"):
        parse_bundle(_good_bundle_yaml(**{"spec.ca_cert": "just some text"}))


def test_ca_cert_always_ends_with_newline() -> None:
    no_trailing_nl = _FAKE_PEM.rstrip("\n")
    bundle = parse_bundle(_good_bundle_yaml(**{"spec.ca_cert": no_trailing_nl}))
    assert bundle.ca_cert_pem.endswith(b"\n")


# ──────────────────────────────────────────────────────────────────────
# Suggested apps


def test_suggested_apps_optional() -> None:
    bundle = parse_bundle(_good_bundle_yaml(**{"spec.suggested_apps": _DELETE}))
    assert bundle.suggested_apps == ()


def test_suggested_apps_must_be_list_of_mappings() -> None:
    with pytest.raises(BundleParseError, match="suggested_apps"):
        parse_bundle(_good_bundle_yaml(**{"spec.suggested_apps": "react-console"}))


def test_suggested_apps_entries_need_name() -> None:
    with pytest.raises(BundleParseError, match="name"):
        parse_bundle(_good_bundle_yaml(**{"spec.suggested_apps": [{"port": 3000}]}))
