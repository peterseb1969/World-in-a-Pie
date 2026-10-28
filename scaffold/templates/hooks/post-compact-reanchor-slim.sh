#!/usr/bin/env bash
# SessionStart[matcher=compact] re-anchor for the slim profile. Plain stdout on
# exit 0 is injected into context before the next turn. In the slim profile a
# compaction does not start a new session: this prints the rules and the
# current phase, and the agent continues where it was.
root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
card="$root/docs/cards/core.md"
phase_file="$root/.claude/.app-phase"

echo "[post-compaction re-anchor] Context was just compacted. Continue in the SAME session —"
echo "do not run /wip-wake. The rules and your phase follow; re-read them before the next step."
echo
if [ -f "$phase_file" ]; then
    phase="$(head -n 1 "$phase_file")"
    echo "Phase: $phase"
    case "${phase##* }" in
        explore|design-model|implement) echo "Phase card: docs/cards/model.md — read it before the next step." ;;
        build-app|improve) echo "Phase card: docs/cards/build.md — read it before the next step." ;;
    esac
else
    echo "Phase: not recorded yet."
fi
echo
if [ -f "$card" ]; then
    # The card's source-hash comment is for the drift test, not for the agent.
    sed '/^<!-- card-sources/,/^-->/d' "$card"
else
    echo "docs/cards/core.md is missing — run the scaffold refresh. Core invariant meanwhile:"
    echo "WIP's primitives are your only data model; metadata.* is never identity, config or schema."
fi
