#!/usr/bin/env python3
"""Per-YAC status line: role, case activity, context usage.

Two modes, both fed JSON on stdin by Claude Code:

  yac-status.py hook   -- hook handler. Records what the YAC is doing in
                          .claude/.yac-activity so the status line can show it.
                          The activity is written by hooks, not by the slash
                          command's prose, so it cannot be skipped by the model.
      UserPromptSubmit   "/wip-case implement 345" typed by the operator sets
                         the activity; any prompt that is not a /wip-* command
                         clears it
      PreToolUse(Skill)  the model invoking the wip-case skill itself
      PostToolUse(Bash)  "kb-write.py CASE_RECORD --patch status=X
                          --match case_number=N" -- the case changed state

  yac-status.py line   -- status line command. Prints one line, e.g.
                          "BE-YAC · implementing CASE-345 · 12m · ctx 42% (84k/200k)"
                          and only the context part when there is no activity.

An activity is stamped with the YAC session id and hidden once that id
changes, so /wip-wake and /wip-setup start every session with a clean line.

Vendored into each YAC clone as .claude/scripts/yac-status.py by the
scaffold. A hook must never break the session, so every failure is
swallowed.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

ACTIVITY_FILE = ".yac-activity"

# /wip-case verbs that mean "working on a case" -> what the status line says.
# list and read are look-ups, not work, so they leave the activity alone.
VERB_LABELS = {
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

CASE_VERB_RE = re.compile(
    r"^\s*/?wip-case\s+(\w+)\s+(?:CASE-)?(\d+)\b", re.IGNORECASE
)
CASE_ARGS_RE = re.compile(r"^\s*(\w+)\s+(?:CASE-)?(\d+)\b", re.IGNORECASE)
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


def write_activity(cdir: Path, label: str, case: str) -> None:
    record = {
        "label": label,
        "case": f"CASE-{case}",
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
    match = None
    if event == "UserPromptSubmit":
        prompt = (payload.get("prompt") or "").lstrip()
        # Any prompt that is not a /wip-* command ends the case activity;
        # other /wip-* commands (status, report, ...) leave it in place.
        if not prompt.startswith("/wip-"):
            (cdir / ACTIVITY_FILE).unlink(missing_ok=True)
            return
        match = CASE_VERB_RE.match(prompt)
    elif event == "PreToolUse" and payload.get("tool_name") == "Skill":
        tool_input = payload.get("tool_input") or {}
        if (tool_input.get("skill") or "").lstrip("/") == "wip-case":
            match = CASE_ARGS_RE.match(tool_input.get("args") or "")
    elif event == "PostToolUse" and payload.get("tool_name") == "Bash":
        command = (payload.get("tool_input") or {}).get("command") or ""
        patch = PATCH_RE.search(command)
        if patch and patch.group(1) in DONE_LABELS:
            write_activity(cdir, DONE_LABELS[patch.group(1)], patch.group(2))
        return
    if match and match.group(1).lower() in VERB_LABELS:
        write_activity(cdir, VERB_LABELS[match.group(1).lower()], match.group(2))


def age(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(seconds // 60, 0)}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def context_part(payload: dict) -> str:
    window = payload.get("context_window") or {}
    used = window.get("used_percentage")
    tokens = window.get("total_input_tokens")
    size = window.get("context_window_size")
    if used is None and tokens and size:
        used = tokens * 100 / size
    if used is None:
        return ""
    part = f"ctx {used:.0f}%"
    if tokens and size:
        part += f" ({tokens // 1000}k/{size // 1000}k)"
    return part


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
        parts.append(f"{record['label']} {record['case']}")
        parts.append(age(int(time.time()) - int(record.get("since", 0))))
    ctx = context_part(payload)
    if ctx:
        parts.append(ctx)
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
    except Exception:  # noqa: BLE001 -- a status helper must never fail the session
        pass


if __name__ == "__main__":
    main()
