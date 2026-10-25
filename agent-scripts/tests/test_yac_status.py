"""The YAC status line: hooks record case activity, the line shows it.

The activity must come from hooks, not from slash-command prose, and it must
never outlive its session or the operator's move to other work. Tests run the
script as a subprocess against a tmp project root (CLAUDE_PROJECT_DIR) with
hook and status-line payloads on stdin, as Claude Code sends them.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "yac_status.py"

SESSION = "BE-YAC-20261025-001010"
CTX = {"context_window": {"used_percentage": 42.3, "total_input_tokens": 84000,
                          "context_window_size": 200000}}


def make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / ".session-role").write_text("BE-YAC\n")
    (root / ".claude" / ".session-id").write_text(SESSION + "\n")
    return root


def run(root: Path, mode: str, payload) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    return subprocess.run([sys.executable, str(SCRIPT), mode], input=stdin,
                          capture_output=True, text=True, env=env, timeout=30)


def prompt(root: Path, text: str) -> None:
    assert run(root, "hook", {"hook_event_name": "UserPromptSubmit", "prompt": text}).returncode == 0


def line(root: Path, payload=None) -> str:
    return run(root, "line", CTX if payload is None else payload).stdout.strip()


def test_idle_line_is_context_only(tmp_path):
    root = make_project(tmp_path)
    assert line(root) == "ctx 42% (84k/200k)"


def test_case_verb_sets_activity(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 345")
    assert line(root) == "BE-YAC · implementing CASE-345 · 0m · ctx 42% (84k/200k)"


def test_case_prefix_in_number_is_accepted(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case respond CASE-456 with a note")
    assert "responding CASE-456" in line(root)


def test_read_shows_the_case_being_read(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 345")
    prompt(root, "/wip-case read 12")
    assert "reading CASE-12" in line(root)


def test_case_command_without_a_case_shows_its_verb(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 345")
    prompt(root, "/wip-case list --status open")
    assert "BE-YAC · wip-case list · " in line(root)


def test_every_wip_command_replaces_the_activity(tmp_path):
    """A finished case must not linger: after "implemented CASE-721", the
    next /wip-* command takes the line over."""
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 721")
    cmd = "kbc kb-write.py CASE_RECORD --patch status=implemented --match case_number=721"
    run(root, "hook", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_input": {"command": cmd}})
    assert "implemented CASE-721" in line(root)
    prompt(root, "/wip-status")
    assert "BE-YAC · wip-status · " in line(root)


def test_wip_command_shows_a_short_first_argument_only(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-report session-end")
    assert "wip-report session-end" in line(root)
    prompt(root, "/wip-report update-session found the scaffold gap")
    assert "wip-report update-session" in line(root)
    prompt(root, "/wip-lesson \"never trust a cached resolve\"")
    assert " wip-lesson · " in line(root)


def test_non_wip_slash_command_clears_activity(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-status")
    prompt(root, "/compact")
    assert line(root) == "ctx 42% (84k/200k)"


def test_model_invoked_wip_skill_sets_activity(tmp_path):
    root = make_project(tmp_path)
    run(root, "hook", {"hook_event_name": "PreToolUse", "tool_name": "Skill",
                       "tool_input": {"skill": "wip-test", "args": "registry"}})
    assert "wip-test registry" in line(root)


def test_non_wip_skill_leaves_activity_alone(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 345")
    run(root, "hook", {"hook_event_name": "PreToolUse", "tool_name": "Skill",
                       "tool_input": {"skill": "simplify", "args": ""}})
    assert "implementing CASE-345" in line(root)


def test_record_from_the_previous_script_still_renders(tmp_path):
    root = make_project(tmp_path)
    (root / ".claude" / ".yac-activity").write_text(json.dumps(
        {"label": "implemented", "case": "CASE-721", "since": 0, "session_id": SESSION}
    ))
    assert "implemented CASE-721" in line(root)


def test_non_wip_prompt_clears_activity(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 345")
    prompt(root, "now look at something else")
    assert line(root) == "ctx 42% (84k/200k)"
    assert not (root / ".claude" / ".yac-activity").exists()


def test_skill_invocation_sets_activity(tmp_path):
    root = make_project(tmp_path)
    run(root, "hook", {"hook_event_name": "PreToolUse", "tool_name": "Skill",
                       "tool_input": {"skill": "wip-case", "args": "close 77"}})
    assert "closing CASE-77" in line(root)


def test_status_patch_records_the_transition(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case respond 456")
    cmd = "kbc kb-write.py CASE_RECORD --patch status=responded --match case_number=456"
    run(root, "hook", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_input": {"command": cmd}})
    assert "responded CASE-456" in line(root)


def test_unrelated_bash_is_ignored(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 345")
    run(root, "hook", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_input": {"command": "git status"}})
    assert "implementing CASE-345" in line(root)


def test_activity_from_an_earlier_session_is_hidden(tmp_path):
    root = make_project(tmp_path)
    prompt(root, "/wip-case implement 345")
    (root / ".claude" / ".session-id").write_text("BE-YAC-20261026-080000\n")
    assert line(root) == "ctx 42% (84k/200k)"


def test_bad_input_never_fails(tmp_path):
    root = make_project(tmp_path)
    assert run(root, "hook", "not json").returncode == 0
    result = run(root, "line", "not json")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_context_turns_red_from_80_percent(tmp_path):
    root = make_project(tmp_path)
    window = {"total_input_tokens": 160000, "context_window_size": 200000}
    assert line(root, {"context_window": {**window, "used_percentage": 80.0}}) == (
        "\033[31mctx 80% (160k/200k)\033[0m"
    )
    assert line(root, {"context_window": {**window, "used_percentage": 79.4}}) == (
        "ctx 79% (160k/200k)"
    )
    # Shown as 80%, so colored as 80%.
    assert line(root, {"context_window": {**window, "used_percentage": 79.6}}).startswith("\033[31m")
