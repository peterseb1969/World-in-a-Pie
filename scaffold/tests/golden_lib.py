"""Golden-snapshot harness for the scaffold scripts (migration step 1).

Runs the REAL scripts (`setup-backend-agent.sh`, `create-app-project.sh`)
against scratch environments, collects the generated surfaces into a
normalized snapshot dict, and compares against committed fixtures in
`golden/`. The fixtures are the regression net for migration steps 2-5:
any refactor of the scripts must reproduce these snapshots byte-identically
(modulo deltas explicitly reviewed and re-captured).

Environment requirements (why these tests are opt-in, see conftest.py):
- macOS/BSD sed — create-app-project.sh uses `sed -i ''` (the very
  portability bug the revamp fixes; until step 4 the golden runs need BSD).
- A single running wip-deploy install (podman) — both scripts detect the
  API key from live container labels; without one the backend script falls
  back nondeterministically and the app script hard-errors by design.
- This clone on `develop` with a provisioned .venv (wip_mcp importable) —
  backend scratch clones symlink to it so no pip install runs.

Determinism notes:
- Tier-3 scenarios pass --kb https://kb.invalid — enable_kb writes kb.json,
  the served-client curl fails (000 → warning path), the /wip-case stub is
  copied. No network mutation, stable output.
- The scaffolds are isolated from the host's WIP install (`_run`): the API
  key path is pinned through WIP_API_KEY_FILE_OVERRIDE, so the output does
  not depend on what the running install is called, and the app create's
  dev-namespace upsert is switched off (WIP_SCAFFOLD_SKIP_DEV_NAMESPACE=1),
  so no run writes to the live install.
- Machine-specific values are normalized: WIP_ROOT, $HOME, the app target
  dir, and the genesis-banner SHA/date stamps.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# Surfaces whose full (normalized) text is snapshotted. Everything else in
# the tree manifest is recorded by path only — straight copies of tracked
# sources (docs, commands) and binaries (tarballs) don't need content
# duplication in fixtures; their names + presence are the contract.
TEXT_SNAPSHOT_PATTERNS = (
    "CLAUDE.md",
    "CLAUDE.md.refresh",
    ".mcp.json",
    ".env",
    ".env.example",
    ".gitignore",
    ".claude/settings.json",
    ".claude/kb.json",
    ".claude/.session-role",
    ".claude/.app-meta",
    ".claude/hooks/post-compact-reanchor.sh",
)

# Tree-manifest exclusions: run artifacts that are not scaffold surfaces.
EXCLUDE_PARTS = {".git", "node_modules", ".venv", "__pycache__"}


# Pinned rather than detected: the fixtures record this path (as
# __HOME__/.wip-deploy/wip-local/...), and both scaffolds honour the
# override. The file need not exist — local mode only writes the path into
# .mcp.json / .env, and with the namespace upsert off nothing reads the key.
HOST_ISOLATION_ENV = {
    "WIP_API_KEY_FILE_OVERRIDE": str(Path.home() / ".wip-deploy/wip-local/secrets/api-key"),
    "WIP_SCAFFOLD_SKIP_DEV_NAMESPACE": "1",
}


def _run(cmd: list[str], cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    env.update(HOST_ISOLATION_ENV)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=600
    )


def _normalize(text: str, roots: dict[str, str]) -> str:
    for token, value in roots.items():
        if value:
            text = text.replace(value, token)
    # Genesis banner stamps: SHA + spawn date vary per capture.
    text = re.sub(
        r"World-in-a-Pie@[0-9a-f]+, spawned \d{4}-\d{2}-\d{2}",
        "World-in-a-Pie@__SHA__, spawned __DATE__",
        text,
    )
    return text


def snapshot_tree(root: Path, roots: dict[str, str]) -> dict:
    """Collect {tree: [...], files: {...}} for a scaffolded directory."""
    tree: list[str] = []
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in EXCLUDE_PARTS for part in rel.parts):
            continue
        if path.is_symlink() and rel.parts[0] == ".venv":
            continue
        if path.is_dir():
            continue
        rel_s = str(rel)
        tree.append(rel_s)
        if rel_s in TEXT_SNAPSHOT_PATTERNS or any(
            rel_s == p for p in TEXT_SNAPSHOT_PATTERNS
        ):
            files[rel_s] = _normalize(path.read_text(errors="replace"), roots)
    return {"tree": tree, "files": files}


def _norm_roots(**kw: str) -> dict[str, str]:
    home = str(Path.home())
    roots = {"__WIP_ROOT__": str(REPO_ROOT)}
    roots.update({k: v for k, v in kw.items()})
    roots["__HOME__"] = home  # substituted last — WIP_ROOT may live under HOME
    return roots


# --- Backend scenarios -------------------------------------------------------

def run_backend_scenario(tmp: Path, *, tier3: bool, refresh: bool) -> dict:
    """Clone this repo to a scratch dir, symlink the real venv, run the
    backend scaffold (twice for refresh), snapshot the generated surfaces."""
    clone = tmp / "wip"
    subprocess.run(
        ["git", "clone", "-q", "--branch", "develop", str(REPO_ROOT), str(clone)],
        check=True, capture_output=True,
    )
    # git clone ships COMMITTED develop — a gate run before committing would
    # silently validate old code against old fixtures (a tautology; exactly
    # that hid a backend delta once). Overlay the WORKING TREE's
    # scaffold-relevant paths so the gate always tests what is about to be
    # committed. App scenarios don't need this: they run the scripts from
    # REPO_ROOT directly.
    for rel in ("scripts", "scaffold/src", "scaffold/templates", "agent-scripts"):
        src, dst = REPO_ROOT / rel, clone / rel
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    # Symlink the provisioned venv: the script sees .venv/bin/python and an
    # importable wip_mcp, so it neither creates a venv nor pip-installs
    # (protects the REAL venv behind the symlink).
    (clone / ".venv").symlink_to(REPO_ROOT / ".venv")

    cmd = ["bash", "scripts/setup-backend-agent.sh"]
    if tier3:
        cmd += ["--kb", "https://kb.invalid"]

    runs = 2 if refresh else 1
    last: subprocess.CompletedProcess | None = None
    for _ in range(runs):
        last = _run(cmd, cwd=clone)
        if last.returncode != 0:
            raise RuntimeError(
                f"backend scaffold failed (rc={last.returncode}):\n"
                f"stdout:\n{last.stdout}\nstderr:\n{last.stderr}"
            )

    roots = _norm_roots(__CLONE__=str(clone))
    snap = snapshot_tree_backend(clone, roots)
    snap["mode_line"] = "Refreshing" if refresh else "Setting up"
    assert last is not None and snap["mode_line"] in last.stdout, (
        f"expected mode '{snap['mode_line']}' in output:\n{last.stdout}"
    )
    return snap


def snapshot_tree_backend(clone: Path, roots: dict[str, str]) -> dict:
    """Backend snapshots only the GENERATED surfaces — the clone itself is
    the whole repo and manifest-ing 5,000 tracked files tells us nothing."""
    surfaces = [
        "CLAUDE.md",
        ".mcp.json",
        ".claude/settings.json",
        ".claude/.session-role",
        ".claude/kb.json",
        ".claude/hooks/post-compact-reanchor.sh",
    ]
    files: dict[str, str] = {}
    for s in surfaces:
        p = clone / s
        if p.exists():
            files[s] = _normalize(p.read_text(errors="replace"), roots)
    commands = sorted(
        p.name for p in (clone / ".claude/commands").glob("*.md")
    )
    scripts = sorted(
        p.name for p in (clone / ".claude/scripts").glob("*")
    )
    return {"files": files, "commands": commands, "scripts": scripts}


# --- App scenarios -----------------------------------------------------------

def run_app_scenario(
    tmp: Path, *, preset: str, tier3: bool, refresh: bool, slug: str
) -> dict:
    app_dir = tmp / slug
    cmd = [
        "bash", str(REPO_ROOT / "scripts/create-app-project.sh"),
        str(app_dir), "--name", f"Golden {slug}", "--prefix", "APP-GLD",
        "--preset", preset,
    ]
    if tier3:
        cmd += ["--kb", "https://kb.invalid"]

    runs = 2 if refresh else 1
    last: subprocess.CompletedProcess | None = None
    for _ in range(runs):
        last = _run(cmd, cwd=REPO_ROOT)
        if last.returncode != 0:
            raise RuntimeError(
                f"app scaffold failed (rc={last.returncode}):\n"
                f"stdout:\n{last.stdout}\nstderr:\n{last.stderr}"
            )

    roots = _norm_roots(__APP_DIR__=str(app_dir))
    snap = snapshot_tree(app_dir, roots)
    assert last is not None
    snap["mode_line"] = "Refreshing" if refresh else "Creating"
    assert snap["mode_line"] in last.stdout, (
        f"expected mode '{snap['mode_line']}' in output:\n{last.stdout}"
    )
    return snap


# --- Fixture I/O -------------------------------------------------------------

def fixture_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def save_fixture(name: str, snap: dict) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path(name).write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")


def load_fixture(name: str) -> dict:
    return json.loads(fixture_path(name).read_text())


def diff_snapshots(expected: dict, actual: dict) -> list[str]:
    """Human-readable list of deltas; empty means identical."""
    deltas: list[str] = []
    for key in ("tree", "commands", "scripts", "mode_line"):
        if key in expected or key in actual:
            e, a = expected.get(key), actual.get(key)
            if e != a:
                if isinstance(e, list) and isinstance(a, list):
                    missing = sorted(set(e) - set(a))
                    extra = sorted(set(a) - set(e))
                    deltas.append(f"{key}: missing={missing} extra={extra}")
                else:
                    deltas.append(f"{key}: expected={e!r} actual={a!r}")
    e_files, a_files = expected.get("files", {}), actual.get("files", {})
    for path in sorted(set(e_files) | set(a_files)):
        if path not in a_files:
            deltas.append(f"files: {path} missing from actual")
        elif path not in e_files:
            deltas.append(f"files: {path} not in fixture")
        elif e_files[path] != a_files[path]:
            import difflib

            d = "\n".join(
                difflib.unified_diff(
                    e_files[path].splitlines(),
                    a_files[path].splitlines(),
                    fromfile=f"fixture/{path}",
                    tofile=f"actual/{path}",
                    lineterm="", n=2,
                )
            )
            deltas.append(f"files: {path} differs:\n{d}")
    return deltas


def cleanup_tmp(tmp: Path) -> None:
    shutil.rmtree(tmp, ignore_errors=True)
