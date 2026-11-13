"""External manifest validator (CASE-353).

Answers "would this `wip-app.yaml` be accepted by the deployer?" for every
rule a manifest can be judged by on its own — without staging it into the
WIP repo first. The rules that need an install (which other apps are enabled
beside it) are named in the output instead of answered.
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
         and `depends_on[]` → must name a discovered component or app, as
         the install's own validators accept
       - `metadata.name` → must not be a component's or the front door's
         (the deployer's rule, spec/validators.py): the app's service would
         replace that one
       - `app_metadata.label` → must not be another app's (excluding
         self when the manifest is already in the repo under the same
         name): the label names the app's origin, <label>.<hostname>.
         Checked against every discovered app; an install checks only the
         enabled ones, so this is the stricter of the two.

  Not answerable here, and named in the CLI's output: namespace
  reservations and OIDC client-id collisions, which depend on the other
  apps an install enables.

Explicitly out of scope (v1):

  - Build context (no Dockerfile checks, no source-tree walking)
  - Env value resolution (the validator doesn't fetch secrets)
  - Live cluster state (offline analysis only)
  - Whether a `from_secret` exists — secret resolution is install-time
    state. Whose it is is checked: the App model refuses a deployer-supplied
    secret the app does not own, and an operator-supplied name that the
    deployer or another app or component in this repo uses is refused here
    (_check_operator_secrets). Names only an install adds (its spec keys,
    its Dex connectors) are refused when the install validates
    (spec/validators.py).
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
    """Run the reference checks. Returns a flat list of errors."""
    errors: list[ManifestValidationError] = []
    component_names = {c.metadata.name for c in components}
    # What an env source or depends_on may name: the install's validators
    # and env resolution accept an app as a target too.
    target_names = component_names | {a.metadata.name for a in discovered_apps}

    # Existing apps in the repo with the same name as the one under
    # validation are NOT a collision — we're validating an update,
    # not a new entry.
    other_apps = [a for a in discovered_apps if a.metadata.name != app.metadata.name]

    errors.extend(_check_name_free(app, component_names))
    errors.extend(_check_env_references(app, target_names))
    errors.extend(_check_operator_secrets(app, components, discovered_apps))
    errors.extend(_check_depends_on(app, target_names))
    errors.extend(_check_route_collisions(app, other_apps))

    return errors


def _check_name_free(app: App, component_names: set[str]) -> list[ManifestValidationError]:
    """An app's name is its service's name on every target, so it cannot be
    a component's or the front door's — the rule spec/validators.py applies
    to every enabled app at install."""
    from wip_deploy.spec.validators import RESERVED_SERVICE_NAMES

    name = app.metadata.name
    if name not in component_names | RESERVED_SERVICE_NAMES:
        return []
    what = "the front door" if name in RESERVED_SERVICE_NAMES else "a component"
    return [ManifestValidationError(
        field="metadata.name",
        message=f"app name {name!r} is the name of {what}",
        hint="rename the app; its service would replace that one",
    )]


def _check_env_references(
    app: App, component_names: set[str]
) -> list[ManifestValidationError]:
    """Each from_component / from_component_host / from_component_port
    must name a discovered component or app. from_secret's ownership is the
    App model's rule; whether the secret exists is install-time state."""
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
                            f"references undiscovered component or app "
                            f"{target!r}"
                        ),
                        hint=_format_available("components and apps", component_names),
                    )
                )

    for i, ev in enumerate(app.spec.env.required):
        _check_one(ev, "required", i)
    for i, ev in enumerate(app.spec.env.optional):
        _check_one(ev, "optional", i)

    return errors


def _secret_owners(
    own: str, components: list[Component], discovered_apps: list[App],
) -> dict[str, str]:
    """Every secret name this repo alone decides, with whose it is: the
    master key, the deployer's generated secrets and system keys, every other
    discovered app's key and passwords, and every component's
    deployer-supplied secret."""
    from wip_deploy.config_gen.api_keys import APP_KEY_PREFIX, MASTER_KEY_SECRET
    from wip_deploy.config_gen.garage import GARAGE_SECRET_NAMES
    from wip_deploy.config_gen.mongodb import PASSWORD_SECRET_PREFIX as MONGO
    from wip_deploy.config_gen.mongodb import ROOT_PASSWORD_SECRET
    from wip_deploy.config_gen.nats import PASSWORD_SECRET_PREFIX as NATS
    from wip_deploy.spec.deployment import SYSTEM_KEY_NAMES

    owners: dict[str, str] = {MASTER_KEY_SECRET: "the install's master key"}
    owners.update(dict.fromkeys((*GARAGE_SECRET_NAMES, ROOT_PASSWORD_SECRET), "a secret the deployer generates"))
    owners.update({f"{key}-api-key": "a key the deployer provisions" for key in SYSTEM_KEY_NAMES})
    for other in (a.metadata.name for a in discovered_apps if a.metadata.name != own):
        for secret in (f"{APP_KEY_PREFIX}{other}-api-key", f"{NATS}{other}", f"{MONGO}{other}"):
            owners[secret] = f"app {other!r}"
    for secret, component in _component_secrets(components):
        owners.setdefault(secret, f"component {component!r}")
    return owners


def _component_secrets(components: list[Component]) -> list[tuple[str, str]]:
    """(secret, component) for every deployer-supplied from_secret a
    component names. Its operator-supplied ones are the operator's."""
    return [
        (ev.source.from_secret, c.metadata.name)
        for c in components
        for ev in (*c.spec.env.required, *c.spec.env.optional)
        if ev.source.from_secret is not None and ev.source.supplied_by != "operator"
    ]


def _generated_family(secret: str) -> str | None:
    """The deployer's generated-name family a secret falls in, if any."""
    from wip_deploy.config_gen.dex import CLIENT_SECRET_PREFIX as DEX_CLIENT
    from wip_deploy.config_gen.dex import PASSWORD_SECRET_PREFIX as DEX_PASSWORD
    from wip_deploy.config_gen.mongodb import PASSWORD_SECRET_PREFIX as MONGO
    from wip_deploy.config_gen.nats import PASSWORD_SECRET_PREFIX as NATS

    return next(
        (f"the deployer's {p}* family" for p in (DEX_PASSWORD, DEX_CLIENT, NATS, MONGO)
         if secret.startswith(p)),
        None,
    )


def _check_operator_secrets(
    app: App, components: list[Component], discovered_apps: list[App],
) -> list[ManifestValidationError]:
    """An operator-supplied `from_secret` must not be a name the deployer or
    another owner in this repo already uses: the install refuses it
    (spec/validators.py, _validate_app_operator_secrets), so the author's
    check says so first. Names an install adds (its spec keys, its Dex
    connectors) are left to the install."""
    owners = _secret_owners(app.metadata.name, components, discovered_apps)
    errors: list[ManifestValidationError] = []
    for section in ("required", "optional"):
        for i, ev in enumerate(getattr(app.spec.env, section)):
            secret = ev.source.from_secret
            if secret is None or ev.source.supplied_by != "operator":
                continue
            owner = owners.get(secret) or _generated_family(secret)
            if owner is None:
                continue
            errors.append(ManifestValidationError(
                field=f"spec.env.{section}[{i}].source.from_secret",
                message=(
                    f"{ev.name} names {secret!r} as operator-supplied, but that "
                    f"is {owner}; the install refuses it"
                ),
                hint="give the secret a name of the app's own",
            ))
    return errors


def _check_depends_on(
    app: App, component_names: set[str]
) -> list[ManifestValidationError]:
    """Every entry in spec.depends_on must be a discovered component or app."""
    errors: list[ManifestValidationError] = []
    for i, dep in enumerate(app.spec.depends_on):
        if dep not in component_names:
            errors.append(
                ManifestValidationError(
                    field=f"spec.depends_on[{i}]",
                    message=(
                        f"references undiscovered component or app {dep!r}"
                    ),
                    hint=_format_available("components and apps", component_names),
                )
            )
    return errors


def _check_route_collisions(
    app: App, other_apps: list[App]
) -> list[ManifestValidationError]:
    """Two apps with one label: each app is served on its own origin,
    <label>.<hostname>, so its routes cannot collide with another app's, but
    two apps with the same label would claim the same origin."""
    errors: list[ManifestValidationError] = []
    owners = {other.app_metadata.label: other.metadata.name for other in other_apps}
    own = app.app_metadata.label
    if own in owners:
        errors.append(
            ManifestValidationError(
                field="app_metadata.label",
                message=f"label {own!r} is already app {owners[own]!r}'s",
                hint="choose a unique label; it names the app's origin, <label>.<hostname>",
            )
        )
    return errors


def _format_available(label: str, names: set[str]) -> str:
    """Render an available-names hint for the error output."""
    if not names:
        return f"no {label} discovered in the WIP root"
    listed = ", ".join(sorted(names))
    return f"available {label}: {listed}"
