Record session work. Three modes; each writes in `reports/<session-id>/`. Let the shell fill the session ID and the time, as the snippets below do — never type them. Tier 3 steps run only when `.claude/kb.json` exists; if kb is unreachable, say so and continue: the local file is what counts.

### `/wip-report session-end` — close the session

Use before Peter runs `/clear`, or at the 300k-token stop rule.

1. Write the summary text — sections only, no frontmatter, no IDs, no times:

   ```bash
   S=$(cat .claude/.session-id)
   cat > "reports/$S/.summary.md" <<'EOF'
   **What happened:** <3–5 sentences: the arc of the session>
   **Dead ends:** <what did not work, and why>
   **Downstream impact:** <changes other apps or the platform will notice — omit if none>
   **Unfinished:** <what is left>
   **For the next YAC:** <what the next session must know to continue>
   EOF
   ```

2. Close the session. The script writes the summary into `session.md`, sets `status: closed` and the end time, and mirrors the session to kb (tier 3). It never overwrites a summary that is already there.

   ```bash
   S=$(cat .claude/.session-id)
   python3 .claude/scripts/wake-rollover.py --close "reports/$S/.summary.md" && rm "reports/$S/.summary.md"
   ```

3. Tier 3: mirror this YAC's memory, if it has any:
   `MEMDIR="$HOME/.claude/projects/$(pwd | sed 's#/#-#g')/memory"; [ -d "$MEMDIR" ] && kbc kb-write.py YAC_MEMORY "$MEMDIR"`

4. Tell Peter the session is closed (`CLOSED=` in the output says how).

### `/wip-report update-session <headline>` — running log

For something worth knowing after a compaction that is not a commit and not a decision: a discovery, a scope trim, a block. Not for "still working".

```bash
S=$(cat .claude/.session-id); f="reports/$S/session-updates.md"
[ -f "$f" ] || printf '# Session Updates — %s\n' "$S" > "$f"
{ printf '\n## %s — ' "$(date '+%H:%M')"; cat <<'EOF'
<headline>
<one paragraph: status, blocker, next step, or the discovery>
EOF
} >> "$f"
```

Tier 3: `kbc kb-write.py SESSION "reports/$(cat .claude/.session-id)/session.md"` (the session stays active).

### `/wip-report` — a decision (fireside)

When Peter and you decide something worth a permanent record: a design choice, a changed direction, a corrected assumption.

```bash
S=$(cat .claude/.session-id); slug=<short-topic-slug>
{ printf -- '---\nsession: %s\ntype: fireside\ntopic: %s\ntime: %s\nparticipants: Peter, %s\n---\n' \
    "$S" "<short topic name>" "$(date '+%Y-%m-%d %H:%M')" "$S"
  cat <<'EOF'

## Context
<what triggered it>

## Decision
<what was decided, and why; the alternatives weighed>

## Peter's Voice
<his words, quoted exactly — omit the section if there are none>
EOF
} > "reports/$S/report-$slug.md"
```

Tier 3: `kbc kb-write.py FIRESIDE "reports/$(cat .claude/.session-id)/report-<short-topic-slug>.md"`.
