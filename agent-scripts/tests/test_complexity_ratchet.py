"""scripts/check-complexity-ratchet.py — the commit-time complexity gate.

Exercised against throwaway git repositories, so every case runs the real
git diff / git show path the CI step runs.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check-complexity-ratchet.py"

_spec = importlib.util.spec_from_file_location("complexity_ratchet", SCRIPT)
ratchet = importlib.util.module_from_spec(_spec)
sys.modules["complexity_ratchet"] = ratchet  # dataclasses resolve their module
_spec.loader.exec_module(ratchet)

AUDITED = "components/registry/src/registry/x.py"


def branches(name: str, n: int) -> str:
    """A function of cyclomatic complexity n + 1 (n independent ifs)."""
    body = "".join(f"    if a == {i}:\n        a += 1\n" for i in range(n))
    return f"def {name}(a):\n{body}    return a\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def commit(repo: Path, files: dict[str, str | None]) -> None:
    """Write (or, for None, delete) files and commit them."""
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c", "--allow-empty")


# ── measure ─────────────────────────────────────────────────────────────


def test_measure_counts_blocks_ranked_c_or_worse_and_their_excess():
    assert ratchet.measure(branches("f", 9)) == ratchet.Measure(0, 0)  # CC 10: rank B
    assert ratchet.measure(branches("f", 10)) == ratchet.Measure(1, 1)  # CC 11: rank C
    assert ratchet.measure(branches("f", 14)) == ratchet.Measure(1, 5)
    assert ratchet.measure(None) == ratchet.Measure(0, 0)


def test_a_class_counts_like_the_audit_but_adds_no_excess():
    method = branches("m", 14).replace("def m(a)", "def m(self, a)")
    source = "class K:\n" + "".join(f"    {line}\n" for line in method.splitlines())
    # The class (rated by its methods' average, 15) and its method are both
    # C+ blocks; only the method's 5 counts as excess.
    assert ratchet.measure(source) == ratchet.Measure(2, 5)


# ── the gate over a commit ──────────────────────────────────────────────


def test_growing_a_function_past_the_slack_fails(repo, capsys):
    commit(repo, {AUDITED: branches("f", 12)})
    commit(repo, {AUDITED: branches("f", 12 + ratchet.EXCESS_SLACK + 1)})
    assert ratchet.main([]) == 1
    out = capsys.readouterr().out
    assert "excess 3 -> 7" in out and "rises by more than 3" in out


def test_a_guard_branch_within_the_slack_passes(repo):
    commit(repo, {AUDITED: branches("f", 12)})
    commit(repo, {AUDITED: branches("f", 12 + ratchet.EXCESS_SLACK)})
    assert ratchet.main([]) == 0


def test_a_commit_that_adds_a_complex_function_fails_even_within_the_slack(repo, capsys):
    commit(repo, {AUDITED: branches("f", 2)})
    commit(repo, {AUDITED: branches("f", 2) + branches("g", 10)})  # CC 11, excess +1
    assert ratchet.main([]) == 1
    assert "adds a function ranked C or worse" in capsys.readouterr().out


def test_a_commit_that_leaves_complexity_alone_passes(repo):
    commit(repo, {AUDITED: branches("f", 20)})
    commit(repo, {AUDITED: "# a comment\n" + branches("f", 20)})
    assert ratchet.main([]) == 0


def test_splitting_one_function_into_several_passes_though_the_count_rises(repo):
    commit(repo, {AUDITED: branches("f", 40)})  # one block, excess 31
    commit(repo, {AUDITED: branches("f1", 12) + branches("f2", 12) + branches("f3", 12)})
    assert ratchet.measure((repo / AUDITED).read_text()) == ratchet.Measure(3, 9)
    assert ratchet.main([]) == 0


def test_a_rise_in_count_without_less_excess_fails(repo):
    commit(repo, {AUDITED: branches("f", 12)})  # 1 block, excess 3
    commit(repo, {AUDITED: branches("f", 11) + branches("g", 10)})  # 2 blocks, 2 + 1
    assert ratchet.main([]) == 1


def test_moving_a_function_into_a_new_module_passes(repo):
    other = "components/registry/src/registry/rules.py"
    commit(repo, {AUDITED: branches("f", 2) + branches("g", 15)})
    commit(repo, {AUDITED: branches("f", 2), other: branches("g", 15)})
    assert ratchet.main([]) == 0


def test_a_rename_is_measured_on_both_sides(repo):
    renamed = "components/registry/src/registry/y.py"
    commit(repo, {AUDITED: branches("g", 15)})
    _git(repo, "mv", AUDITED, renamed)
    _git(repo, "commit", "-q", "-m", "mv")
    assert ratchet.main([]) == 0


def test_deleting_complex_code_passes(repo):
    commit(repo, {AUDITED: branches("g", 15)})
    commit(repo, {AUDITED: None})
    assert ratchet.main([]) == 0


def test_files_outside_the_audited_paths_are_not_measured(repo, capsys):
    outside = "deployer/src/wip_deploy/x.py"
    commit(repo, {outside: branches("f", 2)})
    commit(repo, {outside: branches("f", 30)})
    assert ratchet.main([]) == 0
    assert "no audited Python file changed" in capsys.readouterr().out


def test_without_a_base_commit_there_is_nothing_to_compare(repo):
    commit(repo, {AUDITED: branches("f", 30)})
    assert ratchet.main([]) == 0


def test_a_file_that_does_not_parse_is_skipped_not_measured(repo, capsys):
    commit(repo, {AUDITED: branches("f", 2)})
    commit(repo, {AUDITED: "def broken(:\n"})
    assert ratchet.main([]) == 0
    assert "does not parse" in capsys.readouterr().out


# ── the paths match the audit's ─────────────────────────────────────────


def test_the_gate_measures_the_paths_the_audit_scans():
    audit = (REPO / "scripts" / "quality-audit.sh").read_text()
    step = audit[audit.index("python3 -m radon cc"):audit.index("--min C --json")]
    scanned = tuple(f"{p}/" for p in re.findall(r'"\$ROOT_DIR/([^"]+)"', step))
    assert scanned == ratchet.AUDITED_PATHS
