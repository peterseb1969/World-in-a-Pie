#!/usr/bin/env python3
"""Versions of everything WIP ships, and builds of the libraries apps vendor.

Five packages reach apps as vendored files: the npm tarballs of @wip/client,
@wip/react and @wip/proxy, and the wheels of wip-archive and wip-toolkit. An
app pins each by file name, so a version must never name two different
contents: npm's lockfile hash would break (EINTEGRITY), and pip would keep
running code it already has.

Nobody edits these versions for a fix. A version is computed from git:

    version = <base major>.<base minor>.<base patch + N>

- base: the version written in the package's manifest (package.json or
  pyproject.toml). It changes only at a release (`freeze`) or for a
  deliberate minor or major bump.
- N: the commits since the base was written that touch the package's
  directory. A change to the package moves its version; a change elsewhere
  does not.

A build from content that is not yet on a published branch (develop, or
main, which only ever receives develop) gets a suffix, so it can never take a
number those branches will give to different content: `+dirty.<hash>` for
uncommitted changes, `+local.<tree>` for commits not on develop. A pushed lane
branch counts as local: two lanes that each change a package from the same
base would otherwise both compute the same plain number for different bytes.
A plain patch number, not a pre-release (`-dev.N`, `.devN`), because
npm's `*` and `^` ranges and pip's default resolution both skip pre-releases.

The platform components (the services, auth-gateway, mcp-server, wip-auth
and wip-deploy) ship together as one image set, so they carry ONE version: the
release they were last released in. Nobody edits it for a fix; the release cut
(`release-notes.py cut X.Y.Z`) writes X.Y.Z into every one of them, and
`platform` refuses a tree where they disagree. Between releases a running
service names its exact build in its status payload (sha, image tag).

Usage:
    lib_artifacts.py version [PKG ...]   # the computed version of each package
    lib_artifacts.py build --out DIR     # build all five into DIR; print PKG=PATH lines
    lib_artifacts.py freeze [PKG ...]    # at a release: write each computed version as its base
    lib_artifacts.py platform            # the platform version; exit 1 if the components disagree
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Package:
    name: str
    path: str  # the package directory, relative to the repository root
    kind: str  # "npm" or "wheel"

    @property
    def manifest(self) -> str:
        return f"{self.path}/{'package.json' if self.kind == 'npm' else 'pyproject.toml'}"


# Build order matters: @wip/react builds against @wip/client's dist/.
PACKAGES = {
    p.name: p
    for p in (
        Package("wip-client", "libs/wip-client", "npm"),
        Package("wip-react", "libs/wip-react", "npm"),
        Package("wip-proxy", "libs/wip-proxy", "npm"),
        Package("wip-archive", "libs/wip-archive", "wheel"),
        Package("wip-toolkit", "WIP-Toolkit", "wheel"),
    )
}

_NPM_VERSION = re.compile(r'^(\s*"version"\s*:\s*")([^"]+)(")', re.M)
_PY_VERSION = re.compile(r'^(version\s*=\s*")([^"]+)(")', re.M)
# git log -G patterns: a commit whose diff adds or removes the version line.
_ANCHOR = {"npm": r'^[[:space:]]*"version"[[:space:]]*:', "wheel": r"^version[[:space:]]*="}
_BASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")

# Content reachable from these remote branches is published: it keeps a plain
# number. main only ever receives develop, so a release tag checks out plain too.
PUBLISHED = ("develop", "main")

# Every place a platform component declares its version. All carry one value.
_PY_DUNDER = re.compile(r'^(__version__\s*=\s*")([^"]+)(")', re.M)
PLATFORM_SITES = tuple(
    (path, _PY_DUNDER if path.endswith(".py") else _PY_VERSION)
    for path in (
        "components/registry/src/registry/__init__.py",
        "components/def-store/src/def_store/__init__.py",
        "components/template-store/src/template_store/__init__.py",
        "components/document-store/src/document_store/__init__.py",
        "components/reporting-sync/src/reporting_sync/__init__.py",
        "components/ingest-gateway/src/ingest_gateway/__init__.py",
        "components/auth-gateway/src/auth_gateway/__init__.py",
        "components/mcp-server/pyproject.toml",
        "libs/wip-auth/pyproject.toml",
        "deployer/pyproject.toml",
        "deployer/src/wip_deploy/__init__.py",
    )
)


class LibError(RuntimeError):
    """A version or build that cannot be produced; the message says why."""


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if out.returncode:
        raise LibError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def _pattern(pkg: Package) -> re.Pattern[str]:
    return _NPM_VERSION if pkg.kind == "npm" else _PY_VERSION


def base_version(root: Path, pkg: Package) -> str:
    match = _pattern(pkg).search((root / pkg.manifest).read_text(encoding="utf-8"))
    if not match:
        raise LibError(f"{pkg.manifest}: no version line")
    if not _BASE.fullmatch(match.group(2)):
        raise LibError(f"{pkg.manifest}: base version {match.group(2)!r} is not X.Y.Z")
    return match.group(2)


def _dirty_hash(root: Path, pkg: Package) -> str:
    """Identify uncommitted content: the diff against HEAD plus untracked files."""
    digest = hashlib.sha256(
        subprocess.run(
            ["git", "diff", "HEAD", "--binary", "--", pkg.path],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
    )
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", pkg.path)
    for rel in sorted(filter(None, untracked.splitlines())):
        digest.update(rel.encode() + b"\0" + (root / rel).read_bytes())
    return digest.hexdigest()[:8]


def _suffix(root: Path, pkg: Package) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=all", "--", pkg.path):
        return f"+dirty.{_dirty_hash(root, pkg)}"
    published = [f"--remotes=*/{branch}" for branch in PUBLISHED]
    if _git(root, "rev-list", "-1", "HEAD", "--not", *published, "--", pkg.path):
        return f"+local.{_git(root, 'rev-parse', f'HEAD:{pkg.path}')[:8]}"
    return ""


def compute_version(root: Path, pkg: Package) -> str:
    """The version this tree's content of pkg ships under."""
    if _git(root, "rev-parse", "--is-shallow-repository") == "true":
        raise LibError("shallow clone: the commit count needs full history (git fetch --unshallow)")
    major, minor, patch = (int(x) for x in base_version(root, pkg).split("."))
    anchor = _git(root, "log", "-1", "--format=%H", f"-G{_ANCHOR[pkg.kind]}", "--", pkg.manifest)
    if not anchor:
        raise LibError(f"{pkg.manifest}: no commit wrote its version line")
    count = int(_git(root, "rev-list", "--count", f"{anchor}..HEAD", "--", pkg.path))
    return f"{major}.{minor}.{patch + count}{_suffix(root, pkg)}"


def _set_version(text: str, pkg: Package, version: str) -> str:
    new, n = _pattern(pkg).subn(lambda m: f"{m.group(1)}{version}{m.group(3)}", text, count=1)
    if n != 1:
        raise LibError(f"{pkg.manifest}: no version line")
    return new


# --- building ---------------------------------------------------------------


def _run(cmd: list[str], cwd: Path, what: str) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if out.returncode:
        raise LibError(f"{what} failed in {cwd}:\n{out.stdout}{out.stderr}".rstrip())
    return out.stdout


def _npm_deps(lib: Path) -> None:
    """npm ci when node_modules is missing or older than the lockfile."""
    marker = lib / "node_modules/.package-lock.json"
    lock = lib / "package-lock.json"
    if not marker.exists() or marker.stat().st_mtime < lock.stat().st_mtime:
        _run(["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error"], lib, "npm ci")


def build_npm(root: Path, pkg: Package, version: str, out: Path) -> Path:
    """Build dist/ in place (it is untracked), then pack a staged copy under version."""
    lib = root / pkg.path
    _npm_deps(lib)
    _run(["npm", "run", "build"], lib, "npm run build")
    listing = json.loads(_run(["npm", "pack", "--dry-run", "--json", "--ignore-scripts"], lib, "npm pack --dry-run"))
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for entry in listing[0]["files"]:
            dest = stage / entry["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(lib / entry["path"], dest)
        manifest = stage / "package.json"
        manifest.write_text(_set_version(manifest.read_text(encoding="utf-8"), pkg, version), encoding="utf-8")
        packed = json.loads(
            _run(
                ["npm", "pack", "--json", "--ignore-scripts", "--pack-destination", str(out)],
                stage,
                "npm pack",
            )
        )
    tarball = out / packed[0]["filename"]
    with tarfile.open(tarball, "r:gz") as tf:
        if not any(n.startswith("package/dist/") and n.endswith(".js") for n in tf.getnames()):
            raise LibError(f"{tarball.name} contains no compiled JS in dist/")
    return tarball


_WHEEL_SKIP = shutil.ignore_patterns(
    "dist", "build", ".venv", "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache", ".mypy_cache"
)


def build_wheel(root: Path, pkg: Package, version: str, out: Path) -> Path:
    """Build the wheel from a copy whose pyproject carries version."""
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / Path(pkg.path).name
        shutil.copytree(root / pkg.path, stage, ignore=_WHEEL_SKIP)
        manifest = stage / "pyproject.toml"
        manifest.write_text(_set_version(manifest.read_text(encoding="utf-8"), pkg, version), encoding="utf-8")
        before = set(out.glob("*.whl"))
        _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out), "-q", str(stage)], stage, "wheel build")
    built = set(out.glob("*.whl")) - before
    if len(built) != 1:
        raise LibError(f"{pkg.name}: expected one new wheel in {out}, found {len(built)}")
    return built.pop()


@contextlib.contextmanager
def _build_lock(root: Path):
    """One build at a time per clone: builds write each npm package's dist/ in place."""
    path = Path(_git(root, "rev-parse", "--absolute-git-dir")) / "wip-lib-build.lock"
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def build_all(root: Path, out: Path) -> dict[str, Path]:
    if not shutil.which("npm"):
        raise LibError("npm is not on PATH; it builds @wip/client, @wip/react and @wip/proxy")
    out.mkdir(parents=True, exist_ok=True)
    built: dict[str, Path] = {}
    with _build_lock(root):
        for pkg in PACKAGES.values():
            version = compute_version(root, pkg)
            build = build_npm if pkg.kind == "npm" else build_wheel
            built[pkg.name] = build(root, pkg, version, out)
    return built


def freeze(root: Path, names: list[str]) -> list[str]:
    """At a release: write each package's computed version as its new base."""
    lines = []
    for pkg in (PACKAGES[n] for n in names):
        version = compute_version(root, pkg)
        if "+" in version:
            raise LibError(f"{pkg.name}: {version} is not on develop; commit and push it first")
        path = root / pkg.manifest
        path.write_text(_set_version(path.read_text(encoding="utf-8"), pkg, version), encoding="utf-8")
        lines.append(f"{pkg.name} {version}")
    return lines


def platform_versions(root: Path) -> dict[str, str]:
    """The version each platform site declares."""
    found = {}
    for path, pattern in PLATFORM_SITES:
        match = pattern.search((root / path).read_text(encoding="utf-8"))
        if not match:
            raise LibError(f"{path}: no version line")
        found[path] = match.group(2)
    return found


def platform_version(root: Path) -> str:
    """The one version every platform component carries; refuse a disagreement."""
    found = platform_versions(root)
    values = set(found.values())
    if len(values) != 1:
        lines = "\n".join(f"  {path}: {version}" for path, version in found.items())
        raise LibError(
            "platform components disagree on their version; they carry the release they were "
            "last released in, and only `release-notes.py cut X.Y.Z` changes it — never a fix:\n" + lines
        )
    version = values.pop()
    if not _BASE.fullmatch(version):
        raise LibError(f"platform version {version!r} is not X.Y.Z")
    return version


def set_platform_version(root: Path, version: str) -> None:
    """At a release: write version into every platform site."""
    if not _BASE.fullmatch(version):
        raise LibError(f"platform version {version!r} is not X.Y.Z")
    texts = {}
    for path, pattern in PLATFORM_SITES:
        text = (root / path).read_text(encoding="utf-8")
        new, n = pattern.subn(lambda m: f"{m.group(1)}{version}{m.group(3)}", text, count=1)
        if n != 1:
            raise LibError(f"{path}: no version line")
        texts[path] = new
    for path, text in texts.items():
        (root / path).write_text(text, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=str(REPO), help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("version", "freeze"):
        p = sub.add_parser(name)
        p.add_argument("packages", nargs="*", metavar="PKG", help=", ".join(PACKAGES))
    sub.add_parser("build").add_argument("--out", required=True)
    sub.add_parser("platform")
    return parser


def _dispatch(args: argparse.Namespace, root: Path) -> list[str]:
    if args.cmd == "build":
        return [f"{name}={path}" for name, path in build_all(root, Path(args.out)).items()]
    if args.cmd == "platform":
        return [f"platform {platform_version(root)}"]
    names = args.packages or list(PACKAGES)
    if args.cmd == "version":
        return [f"{name} {compute_version(root, PACKAGES[name])}" for name in names]
    return freeze(root, names)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    unknown = [n for n in getattr(args, "packages", []) if n not in PACKAGES]
    if unknown:
        parser.error(f"unknown package(s) {', '.join(unknown)}; known: {', '.join(PACKAGES)}")
    try:
        lines = _dispatch(args, Path(args.root))
    except LibError as exc:
        print(f"lib_artifacts: {exc}", file=sys.stderr)
        return 1
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
