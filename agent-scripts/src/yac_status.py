#!/usr/bin/env python3
"""Per-YAC status line: role, current /wip-* command, context usage.

Two modes, both fed JSON on stdin by Claude Code:

  yac-status.py hook   -- hook handler. Records what the YAC is doing in
                          .claude/.yac-activity so the status line can show it.
                          The activity is written by hooks, not by the slash
                          command's prose, so it cannot be skipped by the model.
      UserPromptSubmit   every prompt either sets the activity or clears it:
                         a /wip-* command sets it ("/wip-case implement 345"
                         -> "implementing CASE-345", "/wip-report session-end"
                         -> "wip-report session-end"); anything else clears
                         it, so the line never outlives the command it names
      PreToolUse(Skill)  the model invoking a wip-* skill itself sets it too
      PostToolUse(Bash)  "kb-write.py CASE_RECORD --patch status=X
                          --match case_number=N" -- the case changed state

  yac-status.py line   -- status line command. Prints one line, e.g.
                          "BE-YAC · implementing CASE-345 · 12m · ctx 42% (84k/200k)"
                          and only the context part when there is no activity.
                          It also writes the context usage to the project's
                          .claude-context-pct ("42% (84k/200k tokens)"), the
                          file CLAUDE.md's stop rule tells the agent to read:
                          the status line is the only place the numbers exist.

An activity is stamped with the YAC session id and hidden once that id
changes, so /wip-wake and /wip-setup start every session with a clean line.

Both files are local state, never content: the script adds each to the
clone's .git/info/exclude before writing it, so no repo needs a .gitignore
entry for them (app repos scaffolded without one would otherwise show them
as untracked on every refresh).

Vendored into each YAC clone as .claude/scripts/yac-status.py by the
scaffold. A hook must never break the session, so every failure is
swallowed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

ACTIVITY_FILE = ".yac-activity"
CONTEXT_FILE = ".claude-context-pct"

# From this share of the context window on, the context part shows in red:
# time to wrap up the session before compaction does it.
CONTEXT_WARN_PERCENT = 80
# The slim app profile (PROFILE="slim" in .claude/.app-meta) stops at an
# absolute number of tokens used instead: its model's window is large enough
# that 80% of it is far past where the work stays reliable.
SLIM_WARN_TOKENS = 300_000
PROFILE_RE = re.compile(r'^PROFILE="?([\w-]+)"?\s*$', re.MULTILINE)
RED = "\033[31m"
RESET = "\033[0m"

# /wip-case verbs that act on one case -> how the status line names them.
# Any other /wip-* command shows as itself.
VERB_LABELS = {
    "read": "reading",
    "implement": "implementing",
    "respond": "responding",
    "comment": "commenting",
    "close": "closing",
    "reopen": "reopening",
}

# CASE_RECORD status after a transition -> label shown until the next activity.
DONE_LABELS = {
    "implemented": "implemented",
    "responded": "responded",
    "closed": "closed",
    "open": "reopened",
}

WIP_COMMAND_RE = re.compile(r"^\s*/?(wip-[a-z][\w-]*)\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
CASE_VERB_RE = re.compile(r"(\w+)\s+(?:CASE-)?(\d+)\b", re.IGNORECASE)
# A first argument shown beside the command name: one short word, no prose.
SHOWN_ARG_RE = re.compile(r"[A-Za-z][\w.-]{0,23}")
PATCH_RE = re.compile(
    r"CASE_RECORD\s+--patch\s+status=(\w+)\s+--match\s+case_number=(?:CASE-)?(\d+)"
)


def claude_dir(payload: dict) -> Path:
    project = (
        os.environ.get("CLAUDE_PROJECT_DIR")
        or (payload.get("workspace") or {}).get("project_dir")
        or payload.get("cwd")
        or os.getcwd()
    )
    return Path(project) / ".claude"


def wip_activity(text: str) -> str | None:
    """What the status line shows for a /wip-* command, or None for anything else."""
    command = WIP_COMMAND_RE.match(text)
    if not command:
        return None
    name, rest = command.group(1).lower(), command.group(2)
    if name == "wip-case":
        verb = CASE_VERB_RE.match(rest)
        if verb and verb.group(1).lower() in VERB_LABELS:
            return f"{VERB_LABELS[verb.group(1).lower()]} CASE-{verb.group(2)}"
    words = rest.split()
    if words and SHOWN_ARG_RE.fullmatch(words[0]):
        return f"{name} {words[0]}"
    return name


def ensure_git_excluded(project: Path, rel: str) -> None:
    """Add `rel` to the clone's .git/info/exclude unless already listed.

    Only a plain .git directory is handled; a worktree or submodule (.git is
    a file) is left alone — best effort, like everything here.
    """
    git_dir = project / ".git"
    if not git_dir.is_dir():
        return
    exclude = git_dir / "info" / "exclude"
    text = read_text(exclude)
    if f"/{rel}" in text.splitlines():
        return
    exclude.parent.mkdir(exist_ok=True)
    with exclude.open("a") as fh:
        fh.write(("\n" if text else "") + f"/{rel}\n")


def write_activity(cdir: Path, text: str) -> None:
    ensure_git_excluded(cdir.parent, f".claude/{ACTIVITY_FILE}")
    record = {
        "text": text,
        "since": int(time.time()),
        "session_id": read_text(cdir / ".session-id"),
    }
    tmp = cdir / (ACTIVITY_FILE + ".tmp")
    tmp.write_text(json.dumps(record) + "\n")
    tmp.replace(cdir / ACTIVITY_FILE)


def read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def handle_hook(payload: dict) -> None:
    cdir = claude_dir(payload)
    event = payload.get("hook_event_name")
    if event == "UserPromptSubmit":
        activity = wip_activity(payload.get("prompt") or "")
        if activity:
            write_activity(cdir, activity)
        else:
            # Not a /wip-* command: whatever the line showed is no longer
            # what this YAC is doing.
            (cdir / ACTIVITY_FILE).unlink(missing_ok=True)
    elif event == "PreToolUse" and payload.get("tool_name") == "Skill":
        tool_input = payload.get("tool_input") or {}
        skill = (tool_input.get("skill") or "").lstrip("/")
        activity = wip_activity(f"/{skill} {tool_input.get('args') or ''}")
        if activity:
            write_activity(cdir, activity)
    elif event == "PostToolUse" and payload.get("tool_name") == "Bash":
        command = (payload.get("tool_input") or {}).get("command") or ""
        patch = PATCH_RE.search(command)
        if patch and patch.group(1) in DONE_LABELS:
            write_activity(cdir, f"{DONE_LABELS[patch.group(1)]} CASE-{patch.group(2)}")


def age(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(seconds // 60, 0)}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def context_usage(payload: dict) -> tuple[float | None, int | None, int | None]:
    """(percent used, tokens used, window size) from the status-line payload."""
    window = payload.get("context_window") or {}
    used = window.get("used_percentage")
    tokens = window.get("total_input_tokens")
    size = window.get("context_window_size")
    if used is None and tokens and size:
        used = tokens * 100 / size
    if tokens is None and used is not None and size:
        tokens = int(used * size / 100)
    return used, tokens, size


def app_profile(cdir: Path) -> str:
    match = PROFILE_RE.search(read_text(cdir / ".app-meta"))
    return match.group(1) if match else ""


def context_warning(cdir: Path, used: float, tokens: int | None) -> bool:
    if app_profile(cdir) == "slim" and tokens is not None:
        return tokens >= SLIM_WARN_TOKENS
    return int(f"{used:.0f}") >= CONTEXT_WARN_PERCENT  # the number shown decides


def context_part(payload: dict, cdir: Path) -> str:
    used, tokens, size = context_usage(payload)
    if used is None:
        return ""
    part = f"ctx {used:.0f}%"
    if tokens and size:
        part += f" ({tokens // 1000}k/{size // 1000}k)"
    if context_warning(cdir, used, tokens):
        part = f"{RED}{part}{RESET}"
    # The profile sets the warning threshold; showing it makes a profile lost
    # to a refresh visible instead of a silently different threshold.
    profile = app_profile(cdir)
    return f"{profile} {part}" if profile else part


def write_context_file(payload: dict, cdir: Path) -> None:
    """Write the usage to <project>/.claude-context-pct, or remove a stale one.

    The percentage comes first, so a rule like "past 50%" reads it directly;
    the token count serves an absolute limit.
    """
    path = cdir.parent / CONTEXT_FILE
    used, tokens, size = context_usage(payload)
    if used is None:
        path.unlink(missing_ok=True)
        return
    text = f"{used:.0f}%"
    if tokens and size:
        text += f" ({tokens // 1000}k/{size // 1000}k tokens)"
    ensure_git_excluded(cdir.parent, CONTEXT_FILE)
    tmp = cdir.parent / (CONTEXT_FILE + ".tmp")
    tmp.write_text(text + "\n")
    tmp.replace(path)


def status_line(payload: dict) -> str:
    cdir = claude_dir(payload)
    parts = []
    try:
        record = json.loads((cdir / ACTIVITY_FILE).read_text())
    except (OSError, ValueError):
        record = None
    # An activity recorded under an earlier session id belongs to that
    # session; a fresh session starts with nothing to show.
    if record and record.get("session_id") == read_text(cdir / ".session-id"):
        parts.append(read_text(cdir / ".session-role") or "YAC")
        # Records written before "text" existed carry label + case.
        parts.append(record.get("text") or f"{record['label']} {record['case']}")
        parts.append(age(int(time.time()) - int(record.get("since", 0))))
    ctx = context_part(payload, cdir)
    if ctx:
        parts.append(ctx)
    # The line must still print when the project dir is read-only.
    with contextlib.suppress(OSError):
        write_context_file(payload, cdir)
    return " · ".join(parts)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "line"
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    try:
        if mode == "hook":
            handle_hook(payload)
        else:
            print(status_line(payload))
    except Exception:  # a status helper must never fail the session
        pass


if __name__ == "__main__":
    main()
