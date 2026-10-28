"""log-commit.py: the commits.md entry comes from git, not from the model.

Each test builds a real git repository with a session sentinel and runs the
script the way the post-commit hook does — and one test installs the shipped
hook and lets `git commit` fire it.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "agent-scripts" / "src" / "log_commit.py"
HOOK = REPO / "scaffold" / "templates" / "hooks" / "post-commit-log.sh"
SESSION = "APP-TST-20261028-230000"


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Ada Agent", "GIT_AUTHOR_EMAIL": "ada@example.org",
           "GIT_COMMITTER_NAME": "Ada Agent", "GIT_COMMITTER_EMAIL": "ada@example.org"}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True, env=env).stdout


def make_repo(tmp_path: Path, session: bool = True) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "commit.gpgsign", "false")
    if session:
        (root / ".claude").mkdir()
        (root / ".claude" / ".session-id").write_text(SESSION + "\n")
    return root


def commit(root: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        (root / name).write_text(text)
        git(root, "add", name)
    git(root, "commit", "-q", "--no-verify", "-m", message)
    return git(root, "rev-parse", "--short", "HEAD").strip()


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, env=env, timeout=30, cwd=root)


def log(root: Path) -> str:
    return (root / "reports" / SESSION / "commits.md").read_text()


MESSAGE = """feat: add the shelf view

The list page could not group books by shelf, so finding one meant
scrolling the whole library.

Tests: 12 passed, 0 failed
PoNIF: #3 — shelf is not an identity field, so a move is a new version
Co-Authored-By: Someone <someone@example.org>
"""


def test_entry_is_built_from_git(tmp_path):
    root = make_repo(tmp_path)
    short = commit(root, MESSAGE, {"a.txt": "one\ntwo\n", "b.txt": "x\n"})
    assert run(root).returncode == 0
    text = log(root)
    assert f"## {short} — feat: add the shelf view" in text
    assert "**Author:** Ada Agent <ada@example.org>" in text
    assert "**Files:** 2 changed, +3/-0" in text
    assert "**Tests:** 12 passed, 0 failed" in text
    assert "**PoNIF:** #3 — shelf is not an identity field" in text
    assert "**Why:** The list page could not group books by shelf" in text
    assert "Co-Authored-By" not in text
    assert "Discovered" not in text


def test_missing_trailers_say_so(tmp_path):
    root = make_repo(tmp_path)
    commit(root, "fix: typo", {"a.txt": "x\n"})
    run(root)
    text = log(root)
    assert "**Tests:** not recorded" in text
    assert "**Why:** not recorded" in text


def test_why_override(tmp_path):
    root = make_repo(tmp_path)
    commit(root, MESSAGE, {"a.txt": "x\n"})
    run(root, "--why", "Peter asked for shelves.")
    assert "**Why:** Peter asked for shelves." in log(root)


def test_same_hash_is_logged_once(tmp_path):
    root = make_repo(tmp_path)
    short = commit(root, MESSAGE, {"a.txt": "x\n"})
    run(root)
    run(root)
    assert log(root).count(f"## {short} ") == 1


def test_entries_append_in_order(tmp_path):
    root = make_repo(tmp_path)
    first = commit(root, "feat: one", {"a.txt": "1\n"})
    run(root)
    second = commit(root, "feat: two", {"a.txt": "2\n"})
    run(root)
    text = log(root)
    assert text.startswith(f"# Commits — {SESSION}\n")
    assert text.index(f"## {first} ") < text.index(f"## {second} ")


def test_report_only_commit_is_not_logged(tmp_path):
    root = make_repo(tmp_path)
    work = commit(root, "feat: work", {"a.txt": "x\n"})
    run(root)
    git(root, "add", "reports")
    git(root, "commit", "-q", "--no-verify", "-m", "docs(report): close session")
    report_commit = git(root, "rev-parse", "--short", "HEAD").strip()
    run(root)
    text = log(root)
    assert f"## {work} " in text
    assert f"## {report_commit} " not in text
    assert git(root, "status", "--short", "--", "reports").strip() == ""


def test_commit_mixing_reports_and_work_is_logged(tmp_path):
    root = make_repo(tmp_path)
    commit(root, "feat: one", {"a.txt": "1\n"})
    run(root)
    (root / "b.txt").write_text("2\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "feat: two, with the report")
    mixed = git(root, "rev-parse", "--short", "HEAD").strip()
    run(root)
    assert f"## {mixed} " in log(root)


def test_no_session_logs_nothing(tmp_path):
    root = make_repo(tmp_path, session=False)
    commit(root, "feat: one", {"a.txt": "1\n"})
    result = run(root)
    assert result.returncode == 0
    assert "not logged" in result.stderr
    assert not (root / "reports").exists()


def test_the_committed_repo_wins_over_claude_project_dir(tmp_path):
    root = make_repo(tmp_path)
    other = tmp_path / "other"
    (other / ".claude").mkdir(parents=True)
    (other / ".claude" / ".session-id").write_text("BE-YAC-20261028-000000\n")
    short = commit(root, "feat: here", {"a.txt": "x\n"})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(other)}
    subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env,
                   timeout=30, cwd=root, check=True)
    assert f"## {short} " in log(root)
    assert not (other / "reports").exists()


def test_the_shipped_hook_logs_a_real_commit(tmp_path):
    root = make_repo(tmp_path)
    (root / ".claude" / "scripts").mkdir()
    shutil.copy(SCRIPT, root / ".claude" / "scripts" / "log-commit.py")
    hooks = root / ".githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "post-commit")
    (hooks / "post-commit").chmod(0o755)
    git(root, "config", "core.hooksPath", ".githooks")
    (root / "a.txt").write_text("x\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "feat: hooked\n\nBecause.\n\nTests: 1 passed")
    short = git(root, "rev-parse", "--short", "HEAD").strip()
    text = log(root)
    assert f"## {short} — feat: hooked" in text
    assert "**Why:** Because." in text
    assert "**Tests:** 1 passed" in text
