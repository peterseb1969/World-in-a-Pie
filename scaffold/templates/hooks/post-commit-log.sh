#!/bin/sh
# Appends this commit's entry to the session's commits.md
# (reports/<session-id>/commits.md), built from git alone by
# .claude/scripts/log-commit.py: hash, time, author, files and lines from git;
# the subject as the heading, the body as the Why, and the commit message's
# Tests: / PoNIF: / Discovered: trailers as those fields.
#
# The WIP app scaffold ships this file and enables .githooks on every run when
# the clone has no core.hooksPath of its own. By hand:  git config core.hooksPath .githooks
# A post-commit hook cannot fail a commit; this one never tries.
root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
script="$root/.claude/scripts/log-commit.py"
if [ -f "$script" ]; then
    python3 "$script" || true
fi
exit 0
