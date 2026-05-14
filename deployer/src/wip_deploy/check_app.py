"""App-deployability checker (CASE-379-C).

The canonical answer to "is this app ready to be `wip-deploy add-app`'d?"
Extends CASE-353's `validate-manifest` to also check the **app source
repo** — the half of the contract that lives outside the WIP repo and
that today's CASE-375 debugging journey hit four sequential gaps in.

Pattern from CASE-353: pure-function checks, structured results, CLI
prints them as a ✓/✗ list.

### What's checked

For a given app source directory (e.g. `~/Development/WIP-KB/`):

  1. **Dockerfile.dev present** — without it, the dev_simple renderer
     falls back to the production Dockerfile, NODE_ENV gets forced to
     development, and the app's SPA static-asset block is guarded off.
     Result: 404 at the SPA URL.

  2. **vite.config.ts has `host: '0.0.0.0'`** — without it, Vite binds
     only to localhost inside the container; Caddy → container:<vite-port>
     hits ECONNREFUSED.

  3. **vite.config.ts proxy targets match the manifest's PORT** — common
     scaffold mistake is copy-pasting from another app and leaving the
     proxy target on the wrong Express port (e.g., 3001 from clintrial
     when this app's Express runs on 3012). Result: SPA loads, every
     API call returns 500.

  4. **package.json has a `dev` script** — Dockerfile.dev's CMD usually
     is `npm run dev`. No script, no app.

  5. **Manifest exists at `apps/<name>/wip-app.yaml`** — and passes
     CASE-353's validate_manifest. The deployer's whole machinery
     depends on the manifest.

  6. **Manifest declares both `http` and `dev` ports** — CASE-55 contract.
     Without a `dev` port, Caddy routes to Express in dev mode, not Vite,
     and HMR + SPA serving fail.

### What's NOT checked

  - README "Development with wip-deploy" section — optional discipline;
    failure isn't a deployer-side blocker so leaving it as advisory.
  - The platform side (CASE-377 `MCP_ALLOWED_HOST`, CASE-378 router /mcp)
    — those are operator-transparent now; an app authoring against the
    current platform doesn't need to verify them.
  - Live deployment test — that's `wip-deploy add-app` itself. This
    check is the pre-flight.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from wip_deploy.discovery import find_repo_root
from wip_deploy.validate_manifest import (
    ManifestLoadError,
    validate_manifest,
)

# ────────────────────────────────────────────────────────────────────
# Result model
# ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome. `passed=True` is the only success state;
    everything else carries a hint for the operator."""

    name: str
    passed: bool
    message: str
    fix_hint: str | None = None


@dataclass(frozen=True)
class CheckReport:
    """All checks for an app, plus a roll-up."""

    source_dir: Path
    manifest_path: Path | None
    app_name: str | None
    results: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]


# ────────────────────────────────────────────────────────────────────
# Manifest discovery
# ────────────────────────────────────────────────────────────────────


def find_manifest_for_source(
    source_dir: Path, repo_root: Path
) -> tuple[Path | None, str | None, str | None]:
    """Find the `apps/<name>/wip-app.yaml` that matches this source.

    Strategy: read `<source>/package.json`'s `name` field, scan
    `<repo>/apps/*/wip-app.yaml` for a manifest whose `image.name`
    matches. Returns (manifest_path, app_name, ambiguity_note).

    `ambiguity_note` is None on a clean match. If multiple match or
    zero match, it carries a description the operator can act on.
    """
    pkg = source_dir / "package.json"
    if not pkg.is_file():
        return None, None, (
            f"no package.json at {source_dir}/ — can't determine the "
            f"matching app manifest by image name"
        )

    try:
        pkg_data = json.loads(pkg.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, f"could not parse {pkg}: {exc}"

    pkg_name = pkg_data.get("name", "")
    if not pkg_name:
        return None, None, f"{pkg} has no 'name' field"

    # Try direct match: apps/<pkg_name>/wip-app.yaml
    apps_dir = repo_root / "apps"
    direct = apps_dir / pkg_name / "wip-app.yaml"
    if direct.is_file():
        return direct, pkg_name, None

    # Fuzzy match: package.json `name` vs (manifest dir name | metadata.name |
    # image.name). Real-world hyphenation drift is the dominant ambiguity:
    # - react-console: pkg "wip-reactconsole" (no hyphen between react+console)
    #   vs manifest dir "react-console" (with hyphen).
    # - clintrial: pkg "clintrial-explorer" (the SPA) vs manifest dir
    #   "clintrial" (the app handle).
    # Normalize away the "wip-" prefix + all hyphens, then check substring
    # in both directions. Catches both shapes without being too permissive.
    def _normalize(s: str) -> str:
        return s.removeprefix("wip-").replace("-", "").lower()

    pkg_norm = _normalize(pkg_name)

    def _match(candidate: str) -> bool:
        if not candidate:
            return False
        c = _normalize(candidate)
        return c == pkg_norm or c in pkg_norm or pkg_norm in c

    candidates: list[tuple[Path, str]] = []
    for m in sorted(apps_dir.glob("*/wip-app.yaml")):
        try:
            data = yaml.safe_load(m.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        spec = data.get("spec") or {}
        image_name = (spec.get("image") or {}).get("name", "")
        meta_name = (data.get("metadata") or {}).get("name", "")
        dir_name = m.parent.name
        if _match(dir_name) or _match(meta_name) or _match(image_name):
            candidates.append((m, meta_name or dir_name))

    if len(candidates) == 1:
        m, name = candidates[0]
        return m, name, None
    if len(candidates) > 1:
        return None, None, (
            f"ambiguous: package name {pkg_name!r} matches multiple "
            f"app manifests ({', '.join(str(c[0]) for c in candidates)})"
        )
    return None, None, (
        f"no app manifest found for package {pkg_name!r}. Either "
        f"create apps/{pkg_name}/wip-app.yaml, or pass --manifest "
        f"to point at the right one."
    )


# ────────────────────────────────────────────────────────────────────
# Individual checks
# ────────────────────────────────────────────────────────────────────


def check_dockerfile_dev(source_dir: Path) -> CheckResult:
    name = "Dockerfile.dev present at source root"
    if (source_dir / "Dockerfile.dev").is_file():
        return CheckResult(name, True, f"found at {source_dir}/Dockerfile.dev")
    return CheckResult(
        name,
        False,
        f"missing at {source_dir}/Dockerfile.dev",
        fix_hint=(
            "Without Dockerfile.dev, the dev_simple renderer falls back to "
            "the production Dockerfile. NODE_ENV gets forced to development; "
            "the SPA static-asset block (guarded behind NODE_ENV === "
            "'production') is disabled → 404 at /apps/<name>/. "
            "Copy the canonical pattern from apps/react-console or "
            "apps/clintrial-explorer."
        ),
    )


_VITE_HOST_PATTERN = re.compile(
    r"""host\s*:\s*['"]0\.0\.0\.0['"]""", re.MULTILINE
)


def check_vite_host(source_dir: Path) -> CheckResult:
    name = "vite.config.ts binds host: '0.0.0.0'"
    cfg = source_dir / "vite.config.ts"
    if not cfg.is_file():
        # vite.config.js is the JS variant
        cfg_js = source_dir / "vite.config.js"
        if cfg_js.is_file():
            cfg = cfg_js
        else:
            return CheckResult(
                name,
                False,
                f"no vite.config.{{ts,js}} at {source_dir}",
                fix_hint=(
                    "Vite needs a config file. Create vite.config.ts with "
                    "`server.host: '0.0.0.0'` so the dev server binds the "
                    "container's network interface (without it, Caddy → "
                    "container hits ECONNREFUSED)."
                ),
            )
    text = cfg.read_text()
    if _VITE_HOST_PATTERN.search(text):
        return CheckResult(name, True, f"found in {cfg.name}")
    return CheckResult(
        name,
        False,
        f"{cfg.name} doesn't bind host: '0.0.0.0' (Vite defaults to localhost)",
        fix_hint=(
            "Add `server.host: '0.0.0.0'` to your vite.config.ts so Vite "
            "accepts connections from outside the container. Without it, "
            "Caddy → container:<vite-port> hits ECONNREFUSED inside the "
            "container's network."
        ),
    )


def _extract_vite_proxy_targets(text: str) -> list[str]:
    """Return all `'http://localhost:<port>'` proxy target strings found
    in a vite.config.ts. Pattern is intentionally narrow — Vite's proxy
    targets in dev have a very stable shape."""
    pattern = re.compile(r"""['"]http://localhost:(\d+)['"]""")
    return [m.group(0).strip("'\"") for m in pattern.finditer(text)]


def check_vite_proxy_port_matches_manifest(
    source_dir: Path, manifest_path: Path | None
) -> CheckResult:
    name = "vite.config.ts proxy targets match manifest's http port"
    cfg = source_dir / "vite.config.ts"
    if not cfg.is_file():
        return CheckResult(
            name, True,
            "skipped — no vite.config.ts (handled by check_vite_host)",
        )
    if manifest_path is None:
        return CheckResult(
            name, True,
            "skipped — no manifest available to compare ports against",
        )

    try:
        manifest_data = yaml.safe_load(manifest_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return CheckResult(
            name, False, f"could not parse manifest: {exc}",
        )

    spec = manifest_data.get("spec") or {}
    ports = spec.get("ports") or []
    http_port: int | None = None
    for p in ports:
        if isinstance(p, dict) and p.get("name") == "http":
            http_port = p.get("container_port")
            break

    if http_port is None:
        return CheckResult(
            name, False,
            f"manifest {manifest_path} has no 'http' port — can't check "
            f"proxy alignment",
            fix_hint=(
                "Declare `{name: http, container_port: <express-port>}` "
                "in the manifest's spec.ports."
            ),
        )

    text = cfg.read_text()
    targets = _extract_vite_proxy_targets(text)
    if not targets:
        # No proxy section at all — may be intentional for some apps
        return CheckResult(
            name, True,
            "skipped — no `http://localhost:<port>` proxy targets in vite.config.ts",
        )

    expected = f"http://localhost:{http_port}"
    mismatched = [t for t in targets if t != expected]
    if mismatched:
        return CheckResult(
            name, False,
            f"vite proxy points at {sorted(set(mismatched))!r} but the "
            f"manifest's http port is {http_port} (expected {expected!r})",
            fix_hint=(
                f"Common cause: scaffold copy-paste from another app. "
                f"Edit {cfg} so every proxy target is {expected!r}. "
                f"Without this, /apps/<name>/api/* + /server-api/* + /wip/* "
                f"all return 500 (Vite proxy → wrong port → ECONNREFUSED)."
            ),
        )

    return CheckResult(
        name, True,
        f"all vite proxy targets match manifest's http port ({http_port})",
    )


def check_package_dev_script(source_dir: Path) -> CheckResult:
    name = "package.json has a `dev` script"
    pkg = source_dir / "package.json"
    if not pkg.is_file():
        return CheckResult(
            name, False, f"missing package.json at {pkg}",
            fix_hint="This isn't a Node app source dir.",
        )
    try:
        data = json.loads(pkg.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(name, False, f"could not parse {pkg}: {exc}")
    scripts = data.get("scripts") or {}
    if "dev" in scripts:
        return CheckResult(
            name, True, f"`npm run dev` = {scripts['dev']!r}",
        )
    return CheckResult(
        name, False,
        f"{pkg} has no 'dev' script in scripts",
        fix_hint=(
            "Dockerfile.dev's CMD is typically `npm run dev`. Add a `dev` "
            "script — usually `concurrently \"npm run dev:server\" "
            "\"npm run dev:client\"` for split server/client apps."
        ),
    )


def check_manifest_declares_dev_port(
    manifest_path: Path | None,
) -> CheckResult:
    name = "manifest declares both `http` and `dev` ports"
    if manifest_path is None:
        return CheckResult(
            name, False, "no manifest to inspect",
            fix_hint="See the previous check; manifest discovery failed.",
        )
    try:
        data = yaml.safe_load(manifest_path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        return CheckResult(name, False, f"could not parse {manifest_path}: {exc}")
    spec = data.get("spec") or {}
    ports = spec.get("ports") or []
    port_names = {p.get("name") for p in ports if isinstance(p, dict)}
    missing = [n for n in ("http", "dev") if n not in port_names]
    if not missing:
        return CheckResult(
            name, True,
            f"both `http` and `dev` ports declared in {manifest_path}",
        )
    return CheckResult(
        name, False,
        f"manifest {manifest_path} is missing port name(s): {missing}",
        fix_hint=(
            "Add `- {name: dev, container_port: <vite-port>}` (CASE-55) "
            "to spec.ports. Without it, Caddy routes /apps/<name>/* to "
            "Express in dev mode → 404 at the SPA URL."
        ),
    )


def check_manifest_validates(
    manifest_path: Path | None, repo_root: Path
) -> CheckResult:
    name = "manifest passes validate_manifest (CASE-353)"
    if manifest_path is None:
        return CheckResult(name, False, "no manifest found")
    try:
        app, errors = validate_manifest(manifest_path, repo_root)
    except ManifestLoadError as exc:
        return CheckResult(name, False, f"manifest load failed: {exc}")
    if not errors:
        return CheckResult(name, True, "no validation errors")
    return CheckResult(
        name, False,
        f"{len(errors)} validation error(s)",
        fix_hint=(
            "Run `wip-deploy validate-manifest " + str(manifest_path) +
            "` for the full breakdown."
        ),
    )


# ────────────────────────────────────────────────────────────────────
# Public entry
# ────────────────────────────────────────────────────────────────────


def check_app_deployability(
    source_dir: Path,
    manifest_path: Path | None = None,
    repo_root: Path | None = None,
) -> CheckReport:
    """Run all checks against an app source directory.

    `manifest_path` defaults to auto-discovery from the source dir's
    `package.json` name. `repo_root` defaults to the discovered WIP repo
    root (via `find_repo_root`)."""
    source_dir = source_dir.expanduser().resolve()
    if repo_root is None:
        repo_root = find_repo_root()
    else:
        repo_root = repo_root.expanduser().resolve()

    if not source_dir.is_dir():
        return CheckReport(
            source_dir=source_dir,
            manifest_path=manifest_path,
            app_name=None,
            results=[CheckResult(
                "source dir exists", False,
                f"not a directory: {source_dir}",
            )],
        )

    app_name: str | None = None
    ambiguity_note: str | None = None
    if manifest_path is None:
        manifest_path, app_name, ambiguity_note = find_manifest_for_source(
            source_dir, repo_root
        )
    else:
        manifest_path = manifest_path.expanduser().resolve()
        if manifest_path.is_file():
            try:
                data = yaml.safe_load(manifest_path.read_text())
                app_name = (data.get("metadata") or {}).get("name")
            except (OSError, yaml.YAMLError):
                pass

    results: list[CheckResult] = []

    results.append(check_dockerfile_dev(source_dir))
    results.append(check_vite_host(source_dir))
    results.append(check_vite_proxy_port_matches_manifest(source_dir, manifest_path))
    results.append(check_package_dev_script(source_dir))

    if manifest_path is None:
        results.append(CheckResult(
            "matching app manifest discovered",
            False,
            ambiguity_note or "no manifest found",
            fix_hint=(
                "Either create the app manifest at "
                "apps/<name>/wip-app.yaml, or pass --manifest <path>."
            ),
        ))
    else:
        results.append(CheckResult(
            "matching app manifest discovered",
            True,
            f"manifest = {manifest_path}",
        ))
        results.append(check_manifest_declares_dev_port(manifest_path))
        results.append(check_manifest_validates(manifest_path, repo_root))

    return CheckReport(
        source_dir=source_dir,
        manifest_path=manifest_path,
        app_name=app_name,
        results=results,
    )
