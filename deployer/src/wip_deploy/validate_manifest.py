"""External manifest validator (CASE-353).

The canonical answer to "would this `wip-app.yaml` be accepted by the
deployer?" — without staging the manifest into the WIP repo first.
Used by:

  - `wip-deploy validate-manifest <path>` (CLI surface)
  - `/deploy ready` slash command in APP-YACs (wraps this validator;
    Phase 3 of the readiness arc)

Scope (v1, per CASE-353):

  1. Schema validation via the `App` Pydantic model — catches missing
     fields, wrong types, bad enums, validator errors.
  2. Cross-reference validation against the current WIP root's
     discovered components and apps:
       - `from_component` / `from_component_host` / `from_component_port`
         → name must match a discovered component
       - `depends_on[]` → names must match discovered components
       - `routes[].path` → must not collide with another app's
         `app_metadata.route_prefix` (excluding self when the manifest
         is already in the repo under the same name)
       - `app_metadata.route_prefix` → must be a non-empty path

Explicitly out of scope (v1):

  - Build context (no Dockerfile checks, no source-tree walking)
  - Env value resolution (the validator doesn't fetch secrets)
  - Live cluster state (offline analysis only)
  - `from_secret` deep validation — we accept any non-empty secret
    name as plausible. Secret resolution is install-time state, not
    contract-time state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from wip_deploy.discovery import discover
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component

# ────────────────────────────────────────────────────────────────────
# Error model
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ManifestValidationError:
    """A single validation failure with enough context to fix it.

    `field` is a dotted path into the manifest (e.g.
    `spec.env.required[2].source.from_component`). `message` describes
    what went wrong. `hint` is an optional remediation pointer —
    typically the set of valid alternatives discovered against the
    WIP root.
    """

    field: str
    message: str
    hint: str | None = None

    def format(self) -> str:
        prefix = f"  [{self.field}] {self.message}"
        if self.hint:
            return f"{prefix}\n      → {self.hint}"
        return prefix


class ManifestLoadError(Exception):
    """The manifest couldn't be loaded at all (missing file, bad YAML).

    Distinct from `ManifestValidationError` because the validator
    has nothing to report against — there's no parsed manifest. The
    CLI maps this to exit code 2 (vs. 1 for validation failures).
    """

    def __init__(self, path: Path, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.message = message


# ────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────


def resolve_manifest_path(input_path: Path) -> Path:
    """Resolve user input to the actual `wip-app.yaml` to validate.

    Accepts either:
      - the manifest file directly (any name ending in .yaml/.yml)
      - a directory containing a `wip-app.yaml` (the conventional
        location inside `apps/<name>/`)

    Raises ManifestLoadError if neither shape resolves.
    """
    if input_path.is_file():
        return input_path
    if input_path.is_dir():
        candidate = input_path / "wip-app.yaml"
        if candidate.is_file():
            return candidate
        raise ManifestLoadError(
            path=input_path,
            message=f"no `wip-app.yaml` found in directory {input_path}",
        )
    raise ManifestLoadError(
        path=input_path,
        message=f"no such file or directory: {input_path}",
    )


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    """Parse the manifest YAML. Raises ManifestLoadError on parse failure."""
    try:
        raw = manifest_path.read_text()
    except OSError as e:
        raise ManifestLoadError(
            path=manifest_path, message=f"could not read: {e}"
        ) from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ManifestLoadError(
            path=manifest_path, message=f"YAML parse error: {e}"
        ) from e
    if not isinstance(data, dict):
        raise ManifestLoadError(
            path=manifest_path,
            message=(
                f"manifest must be a YAML mapping at the top level, "
                f"got {type(data).__name__}"
            ),
        )
    return data


def validate_manifest(
    manifest_path: Path,
    repo_root: Path,
) -> tuple[App | None, list[ManifestValidationError]]:
    """Run schema + reference validation against the discovered components.

    Returns (app_or_none, errors). When schema validation fails, the
    app is None and reference checks are skipped (the schema must pass
    before downstream checks have a stable object to reason about).
    When schema passes, the app is populated and any cross-reference
    failures appear in the errors list. An empty list means the
    manifest is valid.

    Discovery against `repo_root` is loud: if the WIP repo itself has
    manifest errors (a separate concern from the file under
    validation), we surface those too so the operator knows the
    validator's input is shaky.
    """
    raw = load_manifest(manifest_path)

    # Schema gate — Pydantic enforces required fields, types,
    # enum values, model validators.
    try:
        app = App.model_validate(raw)
    except ValidationError as e:
        errors = [
            ManifestValidationError(
                field=".".join(str(p) for p in err["loc"]) or "<root>",
                message=err["msg"],
                hint=None,
            )
            for err in e.errors()
        ]
        return None, errors

    # Discovery — needed for cross-reference checks. If discovery itself
    # is broken (WIP repo has manifest errors), report those as a
    # surfaced precondition. Callers see "validate the validator's
    # corpus first" rather than silently-incorrect validation.
    discovery = discover(repo_root)
    discovery_errors: list[ManifestValidationError] = []
    if not discovery.ok:
        discovery_errors = [
            ManifestValidationError(
                field="<wip-repo discovery>",
                message=msg,
                hint=(
                    "the WIP root passed to --repo-root has manifest "
                    "errors; fix those before relying on cross-reference "
                    "validation"
                ),
            )
            for msg in discovery.errors
        ]

    ref_errors = _check_references(app, discovery.components, discovery.apps)
    return app, discovery_errors + ref_errors


# ────────────────────────────────────────────────────────────────────
# Cross-reference checks
# ────────────────────────────────────────────────────────────────────


def _check_references(
    app: App,
    components: list[Component],
    discovered_apps: list[App],
) -> list[ManifestValidationError]:
    """Run the four reference checks. Returns a flat list of errors."""
    errors: list[ManifestValidationError] = []
    component_names = {c.metadata.name for c in components}

    # Existing apps in the repo with the same name as the one under
    # validation are NOT a collision — we're validating an update,
    # not a new entry.
    other_apps = [a for a in discovered_apps if a.metadata.name != app.metadata.name]

    errors.extend(_check_env_references(app, component_names))
    errors.extend(_check_depends_on(app, component_names))
    errors.extend(_check_route_collisions(app, other_apps))

    return errors


def _check_env_references(
    app: App, component_names: set[str]
) -> list[ManifestValidationError]:
    """Each from_component / from_component_host / from_component_port
    must name a discovered component. from_secret accepts any non-empty
    string (v1 scope — secret-existence is install-time state)."""
    errors: list[ManifestValidationError] = []

    def _check_one(env_var, section: str, idx: int) -> None:
        src = env_var.source
        for attr in ("from_component", "from_component_host", "from_component_port"):
            target = getattr(src, attr)
            if target is not None and target not in component_names:
                errors.append(
                    ManifestValidationError(
                        field=f"spec.env.{section}[{idx}].source.{attr}",
                        message=(
                            f"references undiscovered component "
                            f"{target!r}"
                        ),
                        hint=_format_available("components", component_names),
                    )
                )

    for i, ev in enumerate(app.spec.env.required):
        _check_one(ev, "required", i)
    for i, ev in enumerate(app.spec.env.optional):
        _check_one(ev, "optional", i)

    return errors


def _check_depends_on(
    app: App, component_names: set[str]
) -> list[ManifestValidationError]:
    """Every entry in spec.depends_on must be a discovered component."""
    errors: list[ManifestValidationError] = []
    for i, dep in enumerate(app.spec.depends_on):
        if dep not in component_names:
            errors.append(
                ManifestValidationError(
                    field=f"spec.depends_on[{i}]",
                    message=(
                        f"references undiscovered component {dep!r}"
                    ),
                    hint=_format_available("components", component_names),
                )
            )
    return errors


def _check_route_collisions(
    app: App, other_apps: list[App]
) -> list[ManifestValidationError]:
    """Route path collisions across apps.

    Two collision shapes:
      1. Two apps declaring the same route path (`spec.routes[].path`).
      2. An app's route_prefix overlapping with another's. The deployer
         doesn't enforce uniqueness at the prefix level today, but
         identical prefixes are an unambiguous error.

    Conservative for v1: only flags exact-path equality between this
    app's routes and other apps' routes, and exact route_prefix
    equality. Sub-prefix-overlap (e.g., /apps/kb vs /apps/kb-internal)
    is not flagged — the renderers handle the resolution order
    deterministically, and false positives would be more annoying
    than the rare real-world overlap.
    """
    errors: list[ManifestValidationError] = []

    # Build a map of {path → owning app name} from the other apps.
    other_paths: dict[str, str] = {}
    other_prefixes: dict[str, str] = {}
    for other in other_apps:
        for route in other.spec.routes:
            other_paths.setdefault(route.path, other.metadata.name)
        other_prefixes.setdefault(
            other.app_metadata.route_prefix, other.metadata.name
        )

    # This app's routes vs other apps' routes.
    for i, route in enumerate(app.spec.routes):
        if route.path in other_paths:
            owner = other_paths[route.path]
            errors.append(
                ManifestValidationError(
                    field=f"spec.routes[{i}].path",
                    message=(
                        f"route {route.path!r} collides with app "
                        f"{owner!r}"
                    ),
                    hint=(
                        f"choose a different path or coordinate with "
                        f"the {owner!r} maintainer"
                    ),
                )
            )

    # This app's route_prefix vs other apps' route_prefixes.
    own_prefix = app.app_metadata.route_prefix
    if own_prefix in other_prefixes:
        owner = other_prefixes[own_prefix]
        errors.append(
            ManifestValidationError(
                field="app_metadata.route_prefix",
                message=(
                    f"route_prefix {own_prefix!r} is already owned by "
                    f"app {owner!r}"
                ),
                hint=(
                    "choose a unique prefix (apps typically live under "
                    "`/apps/<slug>`)"
                ),
            )
        )

    return errors


def _format_available(label: str, names: set[str]) -> str:
    """Render an available-names hint for the error output."""
    if not names:
        return f"no {label} discovered in the WIP root"
    listed = ", ".join(sorted(names))
    return f"available {label}: {listed}"
