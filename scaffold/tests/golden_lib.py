"""Golden-snapshot harness for the scaffold scripts (CASE-612, migration step 1).

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
  back nondeterministically and the app script hard-errors (CASE-558).
- This clone on `develop` with a provisioned .venv (wip_mcp importable) —
  backend scratch clones symlink to it so no pip install runs.

Determinism notes:
- Tier-3 scenarios pass --kb https://kb.invalid — enable_kb writes kb.json,
  the served-client curl fails (000 → warning path), the /wip-case stub is
  copied. No network mutation, stable output.
- App create POSTs the dev namespace to the live install (best-effort in
  the script). The harness deletes `dev-<slug>` afterwards, best-effort —
  a `retain`-mode namespace may refuse deletion; that leaves an empty
  dev-golden-* namespace behind, which is harmless and idempotent across
  re-captures (the script treats non-200 as "may already exist (ok)").
- Machine-specific values are normalized: WIP_ROOT, $HOME, the app target
  dir, and the genesis-banner SHA/date stamps (CASE-415).
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


def _run(cmd: list[str], cwd: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=600
    )


def _normalize(text: str, roots: dict[str, str]) -> str:
    for token, value in roots.items():
        if value:
            text = text.replace(value, token)
    # Genesis banner stamps (CASE-415): SHA + spawn date vary per capture.
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

    _cleanup_namespace(f"dev-golden-{slug}")

    roots = _norm_roots(__APP_DIR__=str(app_dir))
    snap = snapshot_tree(app_dir, roots)
    assert last is not None
    snap["mode_line"] = "Refreshing" if refresh else "Creating"
    assert snap["mode_line"] in last.stdout, (
        f"expected mode '{snap['mode_line']}' in output:\n{last.stdout}"
    )
    return snap


def _cleanup_namespace(prefix: str) -> None:
    """Best-effort: delete the dev namespace the app create POSTed to the
    live install. A retain-mode namespace may refuse — acceptable (empty
    dev-golden-* namespaces are harmless and reused on re-capture)."""
    key_file = _detect_key_file()
    if not key_file:
        return
    try:
        key = Path(key_file).read_text().strip()
        subprocess.run(
            [
                "curl", "-ks", "-X", "DELETE",
                f"https://localhost:8443/api/registry/namespaces/{prefix}",
                "-H", f"X-API-Key: {key}",
            ],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


def _detect_key_file() -> str | None:
    """Same detection the scripts use: single running install's working_dir
    label (CASE-521/539)."""
    try:
        out = subprocess.run(
            ["podman", "ps", "--format", "{{.Labels}}"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return None
    dirs = set(
        re.findall(r"com\.docker\.compose\.project\.working_dir=([^,]*\.wip-deploy[^,]*)", out)
    )
    if len(dirs) == 1:
        kf = Path(next(iter(dirs))) / "secrets/api-key"
        if kf.is_file():
            return str(kf)
    return None


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
