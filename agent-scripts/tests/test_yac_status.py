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


def context_file(root: Path) -> Path:
    return root / ".claude-context-pct"


def test_line_writes_the_context_file(tmp_path):
    """The file CLAUDE.md's stop rule reads; the status line is its only writer."""
    root = make_project(tmp_path)
    line(root)
    assert context_file(root).read_text() == "42% (84k/200k tokens)\n"


def test_context_file_follows_the_latest_refresh(tmp_path):
    root = make_project(tmp_path)
    line(root)
    line(root, {"context_window": {"used_percentage": 61.0, "total_input_tokens": 122000,
                                   "context_window_size": 200000}})
    assert context_file(root).read_text() == "61% (122k/200k tokens)\n"


def test_no_context_data_removes_a_stale_file(tmp_path):
    root = make_project(tmp_path)
    line(root)
    assert run(root, "line", {}).returncode == 0
    assert not context_file(root).exists()


def test_percent_only_payload_still_writes_the_percent(tmp_path):
    root = make_project(tmp_path)
    line(root, {"context_window": {"used_percentage": 37.0}})
    assert context_file(root).read_text() == "37%\n"


def slim(root: Path) -> None:
    (root / ".claude" / ".app-meta").write_text(
        '# app metadata\nAPP_NAME="Cooking"\nPRESET="standard"\nPROFILE="slim"\n'
    )


def test_slim_profile_warns_at_300k_tokens_whatever_the_percent(tmp_path):
    root = make_project(tmp_path)
    slim(root)
    size = 524288
    assert line(root, {"context_window": {"used_percentage": 57.2, "total_input_tokens": 300000,
                                          "context_window_size": size}}).startswith("slim \033[31m")
    assert line(root, {"context_window": {"used_percentage": 57.0, "total_input_tokens": 299000,
                                          "context_window_size": size}}) == "slim ctx 57% (299k/524k)"


def test_slim_profile_does_not_warn_at_80_percent_of_a_small_window(tmp_path):
    root = make_project(tmp_path)
    slim(root)
    assert line(root, {"context_window": {"used_percentage": 90.0, "total_input_tokens": 180000,
                                          "context_window_size": 200000}}) == "slim ctx 90% (180k/200k)"


def test_slim_profile_derives_tokens_from_percent_and_size(tmp_path):
    root = make_project(tmp_path)
    slim(root)
    out = line(root, {"context_window": {"used_percentage": 60.0, "context_window_size": 524288}})
    assert out.startswith("slim \033[31m")  # 60% of 524k is ~314k tokens
    assert context_file(root).read_text() == "60% (314k/524k tokens)\n"


def test_other_profiles_keep_the_percent_rule(tmp_path):
    root = make_project(tmp_path)
    (root / ".claude" / ".app-meta").write_text('APP_NAME="X"\nPROFILE="full"\n')
    window = {"total_input_tokens": 160000, "context_window_size": 200000}
    assert line(root, {"context_window": {**window, "used_percentage": 80.0}}).startswith("full \033[31m")


def test_the_recorded_profile_is_shown(tmp_path):
    root = make_project(tmp_path)
    slim(root)
    assert line(root).startswith("slim ctx 42%")
    (root / ".claude" / ".app-meta").write_text('APP_NAME="X"\nPROFILE="full"\n')
    assert line(root).startswith("full ctx 42%")


def git_project(tmp_path: Path) -> Path:
    root = make_project(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def untracked(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-uall"],
                         capture_output=True, text=True, check=True).stdout
    return [line[3:] for line in out.splitlines() if line.startswith("??")]


def test_written_files_never_show_as_untracked(tmp_path):
    """An app scaffolded without .gitignore entries must not see the status
    files as untracked: the script excludes them in .git/info/exclude."""
    root = git_project(tmp_path)
    prompt(root, "/wip-case read 12")
    line(root)
    assert (root / ".claude-context-pct").exists()
    assert (root / ".claude" / ".yac-activity").exists()
    assert not any(p.endswith((".claude-context-pct", ".yac-activity")) for p in untracked(root))


def test_exclude_entries_are_added_once(tmp_path):
    root = git_project(tmp_path)
    for _ in range(3):
        prompt(root, "/wip-wake")
        line(root)
    lines = (root / ".git" / "info" / "exclude").read_text().splitlines()
    assert lines.count("/.claude-context-pct") == 1
    assert lines.count("/.claude/.yac-activity") == 1


def test_no_git_dir_is_fine(tmp_path):
    root = make_project(tmp_path)
    line(root)
    assert (root / ".claude-context-pct").exists()
    assert not (root / ".git").exists()
