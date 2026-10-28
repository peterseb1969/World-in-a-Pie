"""wake-brief.py: the slim wake's one read, built from files and git.

Each test builds a real git repository shaped like an app clone right after
wake-rollover.py has minted a new session, and runs the script as the slim
/wip-wake does.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "wake_brief.py"
PRIOR = "APP-TST-20261027-100000"
NEW = "APP-TST-20261028-090000"


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@example.org",
           "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@example.org"}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True, env=env).stdout


def session_md(status: str, body: str, continues_from: str | None = None) -> str:
    extra = f"continues_from: {continues_from}\n" if continues_from else ""
    return (f"---\nsession_id: x\nrole: APP-TST\nstarted_at: 2026-10-27T10:00:00\n"
            f"status: {status}\n{extra}---\n{body}")


def make_app(tmp_path: Path, *, prior_body: str = "\n# Session\n\n## Session Summary\n\nDid the list page.\n",
             hooks: bool = True) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "commit.gpgsign", "false")
    if hooks:
        git(root, "config", "core.hooksPath", ".githooks")
    (root / ".claude").mkdir()
    (root / ".claude" / ".session-id").write_text(NEW + "\n")
    (root / "reports" / PRIOR).mkdir(parents=True)
    (root / "reports" / PRIOR / "session.md").write_text(session_md("closed", prior_body))
    (root / "reports" / NEW).mkdir(parents=True)
    (root / "reports" / NEW / "session.md").write_text(session_md("active", "\n", PRIOR))
    (root / "a.txt").write_text("x\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: first")
    return root


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True,
                          text=True, env=env, timeout=30, cwd=root)


def test_prior_summary_git_and_session(tmp_path):
    root = make_app(tmp_path)
    out = run(root).stdout
    assert f"Session: {NEW}" in out
    assert f"Prior session: {PRIOR}" in out
    assert "Did the list page." in out
    assert "feat: first" in out
    assert "Uncommitted: nothing" in out
    assert "WARNING" not in out


def test_phase_with_age_and_card(tmp_path):
    root = make_app(tmp_path)
    (root / ".claude" / ".app-phase").write_text("3 implement\n")
    out = run(root).stdout
    assert "Phase: 3 implement (not yet committed)" in out
    assert "Phase card: docs/cards/model.md" in out
    git(root, "add", ".claude/.app-phase")
    git(root, "commit", "-q", "-m", "chore: phase")
    assert "Phase: 3 implement (set today)" in run(root).stdout


def test_unrecorded_phase_is_not_inferred(tmp_path):
    out = run(make_app(tmp_path)).stdout
    assert "Phase: not recorded" in out


def test_stub_summary_is_reported_not_reconstructed(tmp_path):
    root = make_app(tmp_path, prior_body="\n# S\n\n## Session Summary — auto-closed by /wip-wake (x)\n")
    out = run(root).stdout
    assert "Summary: a stub" in out


def test_logs_are_counted_with_the_latest_headline(tmp_path):
    root = make_app(tmp_path)
    d = root / "reports" / PRIOR
    (d / "commits.md").write_text("# Commits\n\n## abc1234 — one\n\n## def5678 — two\n")
    (d / "session-updates.md").write_text("# Updates\n\n## 10:00 — started\n\n## 11:00 — blocked on X\n")
    out = run(root).stdout
    assert "Commit log: 2 entries" in out
    assert "Running log: 2 entries; latest: 11:00 — blocked on X" in out


def test_brief_open_cases_and_decisions_only(tmp_path):
    root = make_app(tmp_path)
    (root / "reports" / NEW / "project-brief.md").write_text(
        "Project brief: shelf [kb]\n\n### Open cases (1)\n  - CASE-12 [open] Broken sort · 2026-10-01\n\n"
        "### Decisions (1)\n  - DECISION-3 Shelves are terms · 2026-09-01\n\n"
        "### Lessons (1)\n  - LESSON-9 Something · 2026-08-01\n")
    out = run(root).stdout
    assert "CASE-12 [open] Broken sort" in out
    assert "DECISION-3 Shelves are terms" in out
    assert "LESSON-9" not in out


def test_inactive_commit_hook_is_warned(tmp_path):
    out = run(make_app(tmp_path, hooks=False)).stdout
    assert "WARNING: core.hooksPath is not .githooks" in out


def test_explicit_prior_and_fresh_start(tmp_path):
    root = make_app(tmp_path)
    (root / "reports" / NEW / "session.md").write_text(session_md("active", "\n"))
    assert "Prior session: none" in run(root).stdout
    assert f"Prior session: {PRIOR}" in run(root, "--prior", PRIOR).stdout


def test_long_summary_is_capped(tmp_path):
    root = make_app(tmp_path, prior_body="\n## Session Summary\n\n" + "word " * 2000 + "\n")
    out = run(root).stdout
    assert "(truncated" in out
    assert len(out) < 6000
