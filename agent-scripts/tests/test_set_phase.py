"""set-phase.py: the phase is written by the command that starts it.

Tests run the script as a subprocess against a tmp project root
(CLAUDE_PROJECT_DIR), the way a phase command invokes it.
"""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "set_phase.py"


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env, timeout=30)


def phase_file(root: Path) -> Path:
    return root / ".claude" / ".app-phase"


def test_writes_number_and_name(tmp_path):
    result = run(tmp_path, "implement")
    assert result.returncode == 0
    assert phase_file(tmp_path).read_text() == "3 implement\n"


def test_each_phase_has_its_number(tmp_path):
    expected = {"explore": 1, "design-model": 2, "implement": 3, "build-app": 4, "improve": 5}
    for name, number in expected.items():
        assert run(tmp_path, name).returncode == 0
        assert phase_file(tmp_path).read_text() == f"{number} {name}\n"


def test_change_names_the_previous_phase(tmp_path):
    run(tmp_path, "design-model")
    result = run(tmp_path, "implement")
    assert "was: 2 design-model" in result.stdout


def test_same_phase_changes_nothing(tmp_path):
    run(tmp_path, "build-app")
    before = phase_file(tmp_path).stat().st_mtime_ns
    result = run(tmp_path, "build-app")
    assert result.returncode == 0
    assert "unchanged" in result.stdout
    assert phase_file(tmp_path).stat().st_mtime_ns == before


def test_unknown_phase_is_refused_and_nothing_written(tmp_path):
    result = run(tmp_path, "deploy")
    assert result.returncode == 2
    assert "explore" in result.stderr
    assert not phase_file(tmp_path).exists()


def test_no_argument_is_refused(tmp_path):
    assert run(tmp_path).returncode == 2
