"""Parse and validate bootstrap bundle YAML (CASE-373 Phase 1).

A *bootstrap bundle* is a single YAML file that an operator can produce
on the cloud side (hand-crafted in v1, Console-generated in v2+) and
consume on a laptop side to seed an apps-only install. It carries:

  - The cloud's external base URL the apps will connect to
  - A scoped, time-limited API key the apps authenticate with
  - The cloud's internal CA cert bytes (PEM) for TLS trust
  - A suggested-apps list (informational; the operator picks)

This module is the *parser* half. CLI wiring lives in `cli.py`. The
sister module `secrets.py` (file backend) is what eventually owns the
written secret values; this module only validates + returns the
structured data.

Design constraints captured by the schema and enforced here:

  - `permissions` is REQUIRED in `api_key.scope`. No implicit-write
    fallback. FR-YAC's CASE-373 caveat #1 — bundles default to
    least-privilege. Acceptable values are 'read' or 'write'. When
    CASE-351's role taxonomy lands, additional role names join here
    without breaking v1 schema (additive).
  - Bundles with `expires_at` in the past are rejected at parse time.
    Catches the "I scp'd this bundle two years ago" footgun before any
    secret hits disk.
  - The PEM cert is sniffed for its BEGIN/END markers only — full X.509
    validation is out of scope (operators trust the cloud side to ship
    a real cert; we just reject obvious junk).

What this module does NOT do:

  - Verify signatures. v1 bundles ship unsigned (CASE-373 decision #5).
    v1.5 adds signature production + verification.
  - Decide whether to write secrets to disk. That's the CLI's job; this
    module returns a structured `Bundle` and lets the caller decide.

CASE-373 (Phase 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Schema version we accept. v1 ships unsigned; v1.5 will add signature
# fields but preserve this version string (signed bundles are a
# superset, not a fork). When v2 introduces an incompatible change,
# bump this and surface both versions until v1 deprecates.
_SUPPORTED_API_VERSION = "wip.dev/v1"
_SUPPORTED_KIND = "BootstrapBundle"

# Permissions vocabulary v1 accepts. Read vs write maps onto the
# Registry's existing api-key scope model. CASE-351's role taxonomy
# will add named roles ('viewer', 'editor', etc.); when it lands this
# set extends rather than replaces.
_VALID_PERMISSIONS = frozenset({"read", "write"})


class BundleParseError(Exception):
    """Raised when bundle YAML is malformed, missing required fields,
    or violates a schema constraint (expired, bad permissions, etc.).

    Message is operator-facing — callers should print verbatim. The
    `path` attribute (if set) names the offending field for precise
    debugging.
    """

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


@dataclass(frozen=True)
class BundleScope:
    """Resolved scope on a bundle's api-key."""

    namespaces: tuple[str, ...]
    permissions: str  # 'read' or 'write' (v1); CASE-351 adds role names


@dataclass(frozen=True)
class BundleApiKey:
    """Resolved api-key block — the secret value plus its metadata."""

    value: str
    name: str
    scope: BundleScope
    expires_at: datetime  # tz-aware UTC


@dataclass(frozen=True)
class Bundle:
    """Parsed bootstrap bundle, ready for the CLI to act on.

    Field-by-field mapping to the YAML schema is intentional — anyone
    reading the YAML should be able to follow it straight into this
    type.
    """

    name: str
    generated_at: datetime  # tz-aware UTC
    external_base_url: str
    api_key: BundleApiKey
    ca_cert_pem: bytes
    suggested_apps: tuple[str, ...]


def parse_bundle(source: bytes | str | Path) -> Bundle:
    """Parse a bootstrap bundle from raw YAML bytes/string or a file path.

    Args:
        source: Raw YAML bytes, a UTF-8 string, or a Path pointing at
            the bundle file. Path is opened in binary mode.

    Returns:
        A validated `Bundle`.

    Raises:
        BundleParseError: On any schema violation, malformed YAML,
            missing field, or constraint failure (expired, bad
            permissions value, etc.). Message is operator-facing.
    """
    raw = _read_source(source)

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise BundleParseError(f"bundle YAML is malformed: {exc}") from exc

    if not isinstance(doc, dict):
        raise BundleParseError(
            "bundle must be a YAML mapping at the top level, "
            f"got {type(doc).__name__}"
        )

    _require_equals(doc, "api_version", _SUPPORTED_API_VERSION)
    _require_equals(doc, "kind", _SUPPORTED_KIND)

    metadata = _require_mapping(doc, "metadata")
    spec = _require_mapping(doc, "spec")

    name = _require_str(metadata, "metadata.name")
    generated_at = _require_datetime(metadata, "metadata.generated_at")

    external_base_url = _require_str(spec, "spec.external_base_url")

    api_key = _parse_api_key(_require_mapping(spec, "spec.api_key", path="spec.api_key"))
    ca_cert_pem = _parse_ca_cert(spec)
    suggested_apps = _parse_suggested_apps(spec)

    return Bundle(
        name=name,
        generated_at=generated_at,
        external_base_url=external_base_url,
        api_key=api_key,
        ca_cert_pem=ca_cert_pem,
        suggested_apps=suggested_apps,
    )


# ──────────────────────────────────────────────────────────────────────
# Sub-parsers


def _parse_api_key(block: dict) -> BundleApiKey:
    value = _require_str(block, "spec.api_key.value")
    name = _require_str(block, "spec.api_key.name")
    expires_at = _require_datetime(block, "spec.api_key.expires_at")

    if expires_at <= datetime.now(UTC):
        raise BundleParseError(
            f"bundle's api_key expired at {expires_at.isoformat()} — "
            f"refusing to import a stale credential. Generate a fresh bundle.",
            path="spec.api_key.expires_at",
        )

    scope_block = _require_mapping(block, "spec.api_key.scope", path="spec.api_key.scope")
    namespaces_raw = scope_block.get("namespaces")
    if not isinstance(namespaces_raw, list) or not namespaces_raw:
        raise BundleParseError(
            "spec.api_key.scope.namespaces must be a non-empty list",
            path="spec.api_key.scope.namespaces",
        )
    namespaces: list[str] = []
    for i, ns in enumerate(namespaces_raw):
        if not isinstance(ns, str) or not ns:
            raise BundleParseError(
                f"spec.api_key.scope.namespaces[{i}] must be a non-empty string",
                path=f"spec.api_key.scope.namespaces[{i}]",
            )
        namespaces.append(ns)

    # CASE-373 caveat #1: no implicit-write fallback. The field MUST be
    # present and MUST be a recognised value. If we ever loosen this,
    # do it deliberately — not by accident.
    if "permissions" not in scope_block:
        raise BundleParseError(
            "spec.api_key.scope.permissions is required (no default). "
            f"Acceptable values: {sorted(_VALID_PERMISSIONS)}. "
            "Bundles must explicitly opt into write access — see CASE-373 caveat #1.",
            path="spec.api_key.scope.permissions",
        )
    permissions = scope_block["permissions"]
    if not isinstance(permissions, str) or permissions not in _VALID_PERMISSIONS:
        raise BundleParseError(
            f"spec.api_key.scope.permissions must be one of {sorted(_VALID_PERMISSIONS)}, "
            f"got {permissions!r}",
            path="spec.api_key.scope.permissions",
        )

    return BundleApiKey(
        value=value,
        name=name,
        scope=BundleScope(namespaces=tuple(namespaces), permissions=permissions),
        expires_at=expires_at,
    )


def _parse_ca_cert(spec: dict) -> bytes:
    raw = spec.get("ca_cert")
    if not isinstance(raw, str) or not raw.strip():
        raise BundleParseError(
            "spec.ca_cert is required — a PEM-encoded certificate block",
            path="spec.ca_cert",
        )

    pem = raw.strip().encode()
    # Loose validation — we only care that it parses *as PEM*. Full
    # X.509 verification is out of scope; the operator is responsible
    # for the bundle's authenticity (v1.5 adds signature checking).
    if b"-----BEGIN CERTIFICATE-----" not in pem or b"-----END CERTIFICATE-----" not in pem:
        raise BundleParseError(
            "spec.ca_cert does not look like a PEM certificate "
            "(missing BEGIN/END CERTIFICATE markers)",
            path="spec.ca_cert",
        )
    # Always end with a newline so concatenation/append flows behave.
    if not pem.endswith(b"\n"):
        pem = pem + b"\n"
    return pem


def _parse_suggested_apps(spec: dict) -> tuple[str, ...]:
    raw = spec.get("suggested_apps")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise BundleParseError(
            "spec.suggested_apps must be a list of {name: ...} entries",
            path="spec.suggested_apps",
        )
    apps: list[str] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or "name" not in entry:
            raise BundleParseError(
                f"spec.suggested_apps[{i}] must be a mapping with a 'name' field",
                path=f"spec.suggested_apps[{i}]",
            )
        name = entry["name"]
        if not isinstance(name, str) or not name:
            raise BundleParseError(
                f"spec.suggested_apps[{i}].name must be a non-empty string",
                path=f"spec.suggested_apps[{i}].name",
            )
        apps.append(name)
    return tuple(apps)


# ──────────────────────────────────────────────────────────────────────
# Primitives


def _read_source(source: bytes | str | Path) -> bytes:
    if isinstance(source, Path):
        try:
            return source.read_bytes()
        except FileNotFoundError as exc:
            raise BundleParseError(f"bundle file not found: {source}") from exc
        except OSError as exc:
            raise BundleParseError(f"cannot read bundle file {source}: {exc}") from exc
    if isinstance(source, str):
        return source.encode()
    return source


def _require_mapping(doc: dict, key: str, *, path: str | None = None) -> dict:
    value = doc.get(key.split(".")[-1])
    if not isinstance(value, dict):
        raise BundleParseError(
            f"{key} is required and must be a mapping",
            path=path or key,
        )
    return value


def _require_str(doc: dict, key: str) -> str:
    leaf = key.split(".")[-1]
    value = doc.get(leaf)
    if not isinstance(value, str) or not value:
        raise BundleParseError(
            f"{key} is required and must be a non-empty string",
            path=key,
        )
    return value


def _require_equals(doc: dict, key: str, expected: str) -> None:
    actual = doc.get(key)
    if actual != expected:
        raise BundleParseError(
            f"unsupported bundle {key}: expected {expected!r}, got {actual!r}",
            path=key,
        )


def _require_datetime(doc: dict, key: str) -> datetime:
    leaf = key.split(".")[-1]
    value = doc.get(leaf)
    if value is None:
        raise BundleParseError(f"{key} is required (ISO 8601 timestamp)", path=key)

    # PyYAML parses ISO timestamps into datetime objects when possible;
    # if it didn't (string form), try ourselves. Either way we want a
    # tz-aware UTC datetime.
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            # Python 3.11 fromisoformat accepts Z suffix.
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BundleParseError(
                f"{key} is not a valid ISO 8601 timestamp: {value!r}",
                path=key,
            ) from exc
    else:
        raise BundleParseError(
            f"{key} must be an ISO 8601 timestamp, got {type(value).__name__}",
            path=key,
        )

    if dt.tzinfo is None:
        # PyYAML returns naive datetimes for un-suffixed ISO strings;
        # treat those as UTC. This is the same convention `metadata.*`
        # ISO strings use elsewhere in the codebase.
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
