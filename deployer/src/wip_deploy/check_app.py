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

  7. **An HTTP healthcheck's probe binary is in the production image** —
     the rendered probe runs `curl || wget`; without either, the container
     reports permanently unhealthy while it serves fine.

  8. **The production image starts without fetching packages** — the
     start command's runner must be in the image, and a plain `npx` (no
     `--no-install`) fails the check. Otherwise every start downloads the
     runner from the npm registry, and an install without registry access
     cannot start the app. Running it by path (`./node_modules/.bin/<pkg>`)
     is the recommended form.

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
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    # Contract rows this tool cannot check; a pass says nothing about them.
    not_checked: tuple[str, ...] = ()

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

    def _exact(candidate: str) -> bool:
        return bool(candidate) and _normalize(candidate) == pkg_norm

    def _match(candidate: str) -> bool:
        if not candidate:
            return False
        c = _normalize(candidate)
        return c == pkg_norm or c in pkg_norm or pkg_norm in c

    candidates: list[tuple[Path, str, bool]] = []
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
            exact = _exact(dir_name) or _exact(meta_name) or _exact(image_name)
            candidates.append((m, meta_name or dir_name, exact))

    # CASE-562: an exact normalized match outranks substring matches. A
    # parallel-instance manifest (dev-kb, <x>-staging) keeps its
    # distinguishing prefix/suffix through normalization, so it can only
    # ever be a substring match against the base package name — the
    # canonical manifest wins the tie deterministically. Multiple exact
    # matches remain a loud ambiguity.
    exact_candidates = [c for c in candidates if c[2]]
    if len(exact_candidates) == 1:
        m, name, _ = exact_candidates[0]
        return m, name, None

    if len(candidates) == 1:
        m, name, _ = candidates[0]
        return m, name, None
    if len(candidates) > 1:
        pool = exact_candidates or candidates
        return None, None, (
            f"ambiguous: package name {pkg_name!r} matches multiple "
            f"app manifests ({', '.join(str(c[0]) for c in pool)})"
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
            "'production') is disabled → 404 at the app's origin. "
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
        if re.search(r"\bproxy\s*:", text):
            # A proxy section whose targets this pattern cannot read is not
            # a pass: it would let a wrong port through unseen.
            return CheckResult(
                name, False,
                f"{cfg.name} has a proxy section, but no target this check can "
                "read (it reads `'http://localhost:<port>'` literals)",
                fix_hint=(
                    f"Write each proxy target as 'http://localhost:{http_port}', "
                    "the manifest's http port."
                ),
            )
        # No proxy section at all — may be intentional for some apps
        return CheckResult(
            name, True,
            "skipped — vite.config.ts has no proxy section",
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
                f"Without this, the app's /api/* + /server-api/* + /wip/* "
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
            "to spec.ports. Without it, Caddy routes the app's origin to "
            "Express in dev mode → 404 at the SPA URL."
        ),
    )


_DOCKERFILE_FROM = re.compile(r"^\s*FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE)
# A package-manager install, flags allowed before the verb (apk --no-cache add).
_INSTALLS = re.compile(
    r"\b(apk(?:\s+-\S+)*\s+add|apt-get(?:\s+-\S+)*\s+install|apt(?:\s+-\S+)*\s+install)\b"
)


def _runtime_stage(text: str) -> str:
    """The Dockerfile's last stage (from its last FROM), with backslash
    continuations joined so one instruction is one line."""
    text = text.replace("\\\n", " ")
    froms = list(_DOCKERFILE_FROM.finditer(text))
    return text[froms[-1].start():] if froms else text


def _installs_package(stage: str, package: str) -> bool:
    """Whether an install line of ``stage`` names ``package``."""
    return any(
        re.search(rf"\b{re.escape(package)}\b", line[m.end():])
        for line in stage.splitlines()
        if (m := _INSTALLS.search(line))
    )


def check_healthcheck_probe_binary(
    source_dir: Path, manifest_path: Path
) -> CheckResult:
    """An HTTP healthcheck's probe binary must exist in the production image.

    The rendered probe is a shell `curl`/`wget` invocation; an image with
    neither binary exits 127 on every probe and the container reports
    permanently unhealthy while the app serves fine — indistinguishable in
    health status from a genuinely dead app, and the install's health-wait
    fails pointing at the app instead of the missing binary. Alpine bases
    satisfy `wget` via busybox; `node:*-slim` and distroless ship neither.

    Heuristic-grade by design: last `FROM` decides the runtime base, an
    `apk add`/`apt-get install` line naming the binary counts as installed.
    Declaring NO healthcheck is a legal alternative — the container then
    reports no health status at all (plain `Up`, like the platform router).
    """
    name = "healthcheck probe binary present in production image"
    try:
        data = yaml.safe_load(manifest_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return CheckResult(
            name, True, "manifest unreadable — reported by the validate check"
        )
    hc = (data.get("spec") or {}).get("healthcheck") or None
    if not hc or not isinstance(hc, dict):
        return CheckResult(
            name, True,
            "no healthcheck declared — container reports no health status "
            "(plain Up, like the platform router); an allowed choice",
        )
    if hc.get("command"):
        return CheckResult(
            name, True, "exec-style healthcheck — no HTTP probe binary needed"
        )
    probe = str(hc.get("probe", "auto"))

    dockerfile = source_dir / "Dockerfile"
    if not dockerfile.is_file():
        return CheckResult(
            name, False,
            f"HTTP healthcheck declared but no production Dockerfile at "
            f"{dockerfile}",
            fix_hint=(
                "Add the production Dockerfile, or drop spec.healthcheck "
                "from the manifest."
            ),
        )
    try:
        text = dockerfile.read_text()
    except OSError as exc:
        return CheckResult(name, False, f"cannot read {dockerfile}: {exc}")

    stage = _runtime_stage(text)
    froms = _DOCKERFILE_FROM.findall(stage)
    runtime_base = froms[-1] if froms else ""
    base_is_alpine = "alpine" in runtime_base.lower()
    # Only the runtime stage's installs end up in the image the probe runs
    # in; a build stage's curl is gone by then.
    has_curl = bool(_INSTALLS.search(stage)) and _installs_package(stage, "curl")
    has_wget = bool(_INSTALLS.search(stage)) and _installs_package(stage, "wget")

    # busybox (alpine) provides wget but NOT curl — an explicit probe: curl
    # needs a real curl install even on alpine.
    if probe == "curl":
        ok, need = has_curl, "curl"
    elif probe == "wget":
        ok, need = base_is_alpine or has_wget, "wget"
    else:  # auto — the rendered probe chains curl || wget, either satisfies
        ok, need = base_is_alpine or has_curl or has_wget, "curl or wget"

    if ok:
        why = (
            f"runtime base {runtime_base!r} is alpine (busybox wget)"
            if base_is_alpine and not (has_curl or has_wget)
            else "explicit install found in Dockerfile"
        )
        return CheckResult(name, True, why)
    return CheckResult(
        name, False,
        f"manifest declares an HTTP healthcheck (probe: {probe}) but the "
        f"production image (runtime base {runtime_base!r}) shows no {need}",
        fix_hint=(
            f"Install {need} in the runtime stage (~2 MB; e.g. "
            "`RUN apt-get update && apt-get install -y --no-install-recommends "
            "wget && rm -rf /var/lib/apt/lists/*`), switch to an alpine base, "
            "or remove spec.healthcheck — the container then shows plain Up "
            "with no health status, like the platform router. Without one of "
            "these the probe exits 127 and the container reports permanently "
            "unhealthy while the app serves fine."
        ),
    )


_DOCKERFILE_START = re.compile(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", re.MULTILINE | re.IGNORECASE)
_PROD_ONLY_INSTALL = re.compile(
    r"\bnpm\s+(ci|install|i)\b[^\n]*(--omit[= ]dev\b|--production\b|--only[= ]prod)",
    re.IGNORECASE,
)
_NODE_MODULES_COPY = re.compile(r"COPY\s+--from=\S+\s+\S*node_modules", re.IGNORECASE)
# A node_modules/.bin runner, relative or absolute (/app/node_modules/.bin/x).
_LOCAL_BIN = re.compile(r"^(?:\./|/(?:[^/]+/)*)?node_modules/\.bin/([^/]+)$")


def _start_tokens(stage: str) -> list[str]:
    """The tokens of the stage's last CMD (or ENTRYPOINT), exec or shell form."""
    matches = _DOCKERFILE_START.findall(stage)
    if not matches:
        return []
    instruction = next((m for m in reversed(matches) if m[0].upper() == "CMD"), matches[-1])
    raw: str = str(instruction[1]).strip()
    if raw.startswith("["):
        try:
            tokens = json.loads(raw)
            return [str(t) for t in tokens] if isinstance(tokens, list) else []
        except ValueError:
            return []
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _package_json(source_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((source_dir / "package.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# Commands an npm script runs that are not a package's bin.
_NOT_BINS = frozenset({"node", "npx", "npm", "sh", "bash", "exec", "env"})


def _npm_script_tokens(tokens: list[str], pkg: dict[str, Any]) -> list[str]:
    """`npm start` / `npm run <script>` start what package.json's script
    says: its tokens, so the runner it calls is checked too."""
    if len(tokens) < 2 or tokens[0] != "npm":
        return tokens
    if tokens[1] == "start":
        script = "start"
    elif tokens[1] in ("run", "run-script") and len(tokens) > 2:
        script = tokens[2]
    else:
        return tokens
    command = (pkg.get("scripts") or {}).get(script)
    if not isinstance(command, str):
        return tokens
    try:
        inner = shlex.split(command)
    except ValueError:
        inner = command.split()
    # npm runs a script with node_modules/.bin on the PATH: a bare command
    # there is a package's bin, unless it is the shell's or node itself.
    if inner and inner[0] not in _NOT_BINS and "/" not in inner[0]:
        inner = [f"node_modules/.bin/{inner[0]}", *inner[1:]]
    return inner


def _package_providing(runner: str, source_dir: Path, deps: dict[str, Any]) -> str | None:
    """The dependency that provides the `runner` bin: one of that name, or
    one whose installed package.json declares it as a bin (tsc is
    typescript's). None when neither says."""
    if runner in deps:
        return runner
    for name in deps:
        try:
            meta = json.loads((source_dir / "node_modules" / name / "package.json").read_text())
        except (OSError, ValueError):
            continue
        bins = meta.get("bin") if isinstance(meta, dict) else None
        if isinstance(bins, dict) and runner in bins:
            return str(name)
        if isinstance(bins, str) and str(name).rsplit("/", 1)[-1] == runner:
            return str(name)
    return None


def check_runtime_fetches_nothing(source_dir: Path) -> CheckResult:
    """The production image starts without downloading packages.

    `npx <pkg>` downloads <pkg> from the npm registry when the image does not
    contain it, at every container start. An install without registry access
    (air-gapped, a firewalled cluster, an npm outage) then cannot start the
    app, and what runs is whatever version the registry serves. Two shapes
    fail:

    - `npx` in the start command without `--no-install`: a missing runner
      becomes a silent download instead of a start failure. `npx
      --no-install` passes, since it installs nothing, but the hints
      recommend the runner's path: with the runner missing, `--no-install`
      still asks the registry before failing (offline, after DNS retries);
    - the runner is only a devDependency while the final stage installs
      production dependencies only (`npm ci --omit=dev` or `--production`)
      and copies no node_modules from a full install: it is certain to be
      missing.

    Heuristic-grade, like the probe-binary check: the last `FROM` begins the
    runtime stage, its last CMD (or ENTRYPOINT) is the start command, and the
    runner is the package `npx` names or a `node_modules/.bin` path.
    """
    name = "production image starts without fetching packages"
    dockerfile = source_dir / "Dockerfile"
    if not dockerfile.is_file():
        return CheckResult(name, True, "no production Dockerfile — nothing to check")
    try:
        text = dockerfile.read_text()
    except OSError as exc:
        return CheckResult(name, False, f"cannot read {dockerfile}: {exc}")

    stage = _runtime_stage(text)
    pkg = _package_json(source_dir)
    tokens = _npm_script_tokens(_start_tokens(stage), pkg)

    runner: str | None = None
    via_npx = False
    no_install = False
    if "npx" in tokens:
        via_npx = True
        rest = tokens[tokens.index("npx") + 1:]
        flags = [t for t in rest if t.startswith("-")]
        no_install = "--no-install" in flags
        runner = next((t for t in rest if not t.startswith("-")), None)
    else:
        for token in tokens:
            match = _LOCAL_BIN.match(token)
            if match:
                runner = match.group(1)
                break
    if runner is None:
        return CheckResult(name, True, "start command runs no npx or node_modules/.bin runner")

    command = " ".join(tokens)
    if via_npx and not no_install:
        return CheckResult(
            name, False,
            f"start command `{command}` runs npx without --no-install: if "
            f"{runner} is ever missing from the image, every start downloads it "
            "from the npm registry",
            fix_hint=(
                f"Run it by path, `./node_modules/.bin/{runner} …`, and keep "
                f"{runner} in the image: list it under `dependencies` in "
                "package.json, since the image runs it. A missing runner then "
                "fails the start at once, naming the file, and npm is never "
                "involved. (`npx --no-install` also installs nothing, but with "
                "the runner missing it still asks the registry before failing.)"
            ),
        )

    deps: dict[str, Any] = pkg.get("dependencies") or {}
    dev_deps: dict[str, Any] = pkg.get("devDependencies") or {}
    package = _package_providing(runner, source_dir, {**dev_deps, **deps})
    if package is None:
        return CheckResult(
            name, True,
            f"not checked — start command `{command}` runs {runner}, and no "
            "dependency in package.json is known to provide it (install "
            "node_modules and re-run to map a bin to its package)",
        )
    runner = package
    prod_only = bool(_PROD_ONLY_INSTALL.search(stage)) and not _NODE_MODULES_COPY.search(stage)
    if runner in dev_deps and runner not in deps and prod_only:
        return CheckResult(
            name, False,
            f"start command `{command}` needs {runner}, a devDependency, but the "
            "runtime stage installs production dependencies only: the image "
            f"does not contain {runner}",
            fix_hint=(
                f"Move {runner} to `dependencies` in package.json (the image runs "
                "it), or build the server in the build stage and run it with "
                "plain `node`."
            ),
        )
    return CheckResult(name, True, f"{runner} runs from the image ({command})")


def check_manifest_validates(
    manifest_path: Path | None, repo_root: Path
) -> CheckResult:
    name = "manifest passes validate_manifest (CASE-353)"
    if manifest_path is None:
        return CheckResult(name, False, "no manifest found")
    try:
        _app, errors = validate_manifest(manifest_path, repo_root)
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


# The app contract's rows (docs/wip-deployable-app-contract.md) this tool
# does not read: they need the running app or the app's own code paths.
NOT_CHECKED = (
    "`.dockerignore` excludes node_modules, dist, *.log and .git",
    "the server sets `app.set('trust proxy', 1)` (HTTPS terminates at the edge)",
    "`GET /api/app-config` returns the client-visible config (at least `namespace`)",
)


def check_production_dockerfile(source_dir: Path) -> CheckResult:
    """The app ships a production Dockerfile: wip-deploy builds the image
    from it, and the Dockerfile checks below read it."""
    name = "production Dockerfile present"
    if (source_dir / "Dockerfile").is_file():
        return CheckResult(name, True, "Dockerfile")
    return CheckResult(
        name, False, f"no Dockerfile in {source_dir}",
        fix_hint="Add the production Dockerfile: the deployer builds the app's image from it.",
    )


def _default_repo_root() -> Path:
    """WIP_REPO_ROOT, else the repo this deployer runs from, else the one
    above the working directory. Run from an app's own checkout, the
    working directory's .git would otherwise be taken for WIP's."""
    env = os.environ.get("WIP_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "components").is_dir() and (ancestor / "deployer").is_dir():
            return ancestor
    return find_repo_root()


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
    repo_root = (
        _default_repo_root() if repo_root is None else repo_root.expanduser().resolve()
    )

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
        if not manifest_path.is_file():
            ambiguity_note = f"--manifest {manifest_path} does not exist"
            manifest_path = None
        else:
            try:
                data = yaml.safe_load(manifest_path.read_text())
                app_name = (data.get("metadata") or {}).get("name")
            except (OSError, yaml.YAMLError):
                pass

    results: list[CheckResult] = []

    results.append(check_production_dockerfile(source_dir))
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
        results.append(check_healthcheck_probe_binary(source_dir, manifest_path))

    results.append(check_runtime_fetches_nothing(source_dir))

    return CheckReport(
        source_dir=source_dir,
        manifest_path=manifest_path,
        app_name=app_name,
        results=results,
        not_checked=NOT_CHECKED,
    )
