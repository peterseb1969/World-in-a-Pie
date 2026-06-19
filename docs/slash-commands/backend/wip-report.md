Capture session-meaningful work for the Field Reporter.

Three modes. Each writes to a distinct file in your session report dir at `reports/<your-session-id>/`. Pick by what you're capturing, not by how you feel.

| Invocation | Mode | File written | Convention |
|---|---|---|---|
| `/wip-report` (no arg) | Fireside | `report-<slug>.md` (one per decision) | Decision artifact: design discussions, choice points, alternatives weighed, rationale |
| `/wip-report update-session [optional terse note]` | Running log | `session-updates.md` (append-only) | Session-meaningful work that is neither a change, end-state, nor decision |
| `/wip-report session-end` | Wrap-up | `session.md` (Session Summary section, overwritten) | End-of-work synthesis: what happened, dead ends, downstream impact, unfinished, for-the-next-YAC |

### Picking a mode before context resets

Two distinct events look like "session ending" but have different recovery semantics. Pick by what comes next:

| Next event | Same agent? | Use this mode | Why |
|---|---|---|---|
| `/compact` | Yes — conversation summarized; agent identity persists | **Mode 2** (running log) | Post-compaction self reads `session-updates.md` and picks up where the pre-compacted self left off. A Session Summary is wasted effort — it'd be re-overwritten next compaction. |
| `/clear` | No — conversation reset; agent re-reads CLAUDE.md cold | **Mode 3** (session-end) | The post-clear agent has no in-conversation memory; the Session Summary is the artifact it starts from. |
| End-of-day / new session | No — next session is a different `<PREFIX>-YYYYMMDD-HHMM` agent | **Mode 3** (session-end) | Same reason — next agent reads cold from `session.md` + `commits.md` + `session-updates.md`. |

Reflex check: if the agent identity persists across the event, you want the running log. If a fresh agent starts from durable artifacts, you want the wrap-up.

### Prerequisites

Your session ID lives in `.claude/.session-id` (written by `/wip-setup` or `/wip-wake`). Read it — `cat "$CLAUDE_PROJECT_DIR/.claude/.session-id"` (fall back to `$PWD/.claude/.session-id`) — and use that value as `<SESSION-ID>` everywhere below; the report dir is `reports/<SESSION-ID>/`. If `.claude/.session-id` is missing, run `/wip-setup` (fresh) or `/wip-wake` (continuation) first — never hand-mint an ID.

---

## Mode 1 — Bare `/wip-report` (fireside)

Use for design decisions worth a permanent record.

### Steps

1. Get the current time:

   ```bash
   date '+%Y-%m-%d %H:%M'
   ```

2. Identify the topic. Infer from context. If unclear, ask Peter. Create a short slug: `namespace-deletion-design`, `mutable-terminologies`, `scope-change-auth`.

3. Create a file at `reports/<YOUR-SESSION-ID>/report-<topic-slug>.md` with this structure:

   ```markdown
   ---
   session: <your session ID>
   type: fireside
   topic: <short topic name>
   time: <from date command above>
   participants: Peter, <your session ID>
   ---

   ## Context
   <What triggered this discussion>

   ## Options Considered
   <Alternatives discussed, if any>

   ## Decision
   <What was decided and why>

   ## Deferred
   <What was explicitly left open>

   ## Peter's Voice
   <Direct quotes — corrections, challenges, insights. Omit if nothing quotable.>

   ## Impact
   <How this affects current work, other apps, or the platform>
   ```

4. **Mirror the fireside to kb (tier 3 only, warn-and-continue)** — **Tier gate (CASE-463):** runs only in tier-3 repos; if `.claude/kb.json` is absent, skip this step silently (tier-2 solo mode is by design). A fireside is a **first-class, findable FIRESIDE entity** (CASE-479) — not session-body sediment. Compose the file locally first (step 3), then:

   ```bash
   KB_URL="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')"; KB_KEYFILE="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')"
   python3 -c 'import json,sys; print(json.dumps({"body":open(sys.argv[1]).read(),"session_id":"<YOUR-SESSION-ID>"}))' "reports/<YOUR-SESSION-ID>/report-<topic-slug>.md" \
     | curl -fsSk -X POST "$KB_URL/apps/kb/server-api/kb/firesides/mirror" -H "X-API-Key: $(cat "$KB_KEYFILE")" -H "Content-Type: application/json" -d @-
   ```

   The gateway derives `title`/`topic`/`authored_by`/`chat_date` from the fireside frontmatter (`topic`, `participants`, `time`) and upserts by `title`, so re-running is idempotent (PoNIF #3). If kb is unreachable, log to stderr and **proceed** — the local file is authoritative; re-run the same POST to retry.

5. Tell Peter the report was written and what file it's in. Continue with the session's work.

### When to use Mode 1

- Peter says "let's talk about..." or initiates a design discussion
- A mid-implementation decision changes direction
- Peter corrects an assumption or challenges an approach
- A cross-app or platform-level issue is identified

### When NOT to use Mode 1

- Routine bug fixes → use `commits.md`
- Standard phase work → use `session.md`
- Session-meaningful work without a decision shape → use Mode 2 (`/wip-report update-session`)
- Factual Q&A without broader implications → none of these
- Peter said "off the record" → don't report

---

## Mode 2 — `/wip-report update-session [optional terse note]` (running log)

Use for session-meaningful work that is **neither a change, an end-state, nor a fireside-grade decision**. Three trigger categories:

1. **Discovery without a commit anchor.** Something you learned while reading or exploring that isn't about any specific commit you're about to make. Example: "templates/bootstrap/bootstrap.server.ts.template imports ./wip-api.js and ./lib/sse.js; neither file exists in the scaffold."
2. **Scope-trim decision mid-session.** Why you're doing less than originally pitched, when the rationale isn't architectural enough for a fireside but matters for reading the resulting commit. Example: "Trimmed Step 2 to seed-files-only because the BootstrapGate wiring requires scaffolding that does not yet exist."
3. **Block/unblock state and pre-compaction snapshots.** "Blocked on X waiting for Y." Pre-`/compact` "where I am now" written when context is filling — so the post-compaction same-agent self has more than just the last commit message and a stale session.md. (For `/clear` or end-of-day instead, use Mode 3 — see the "Picking a mode" table at the top.)

**Do NOT use for:**

- Routine "still working" updates — those belong in chat, not the log.
- Change-in-tree — use `commits.md`.
- End-of-session wrap — use `/wip-report session-end`.
- Decision artifacts — use Mode 1.

### Entry format

Append to `reports/<session-id>/session-updates.md`. Each entry is **timestamp + short headline + one paragraph**:

```
## HH:MM — short headline
<one-paragraph snapshot: status, blockers, next step, any non-commit-anchored discovery worth surfacing>
```

If no `session-updates.md` exists yet, create it with this header at the top:

```
# Session Updates — <session-id>

Append-only running log. Distinct from session.md (overwritten at end) and report-<slug>.md (per-decision). Read by /wip-wake after session.md and commits.md.
```

### Mirror to kb (tier 3 only, warn-and-continue)

After appending the entry, push the running log to kb so its kb copy is never stale — **every** update-session, not a selected subset. **Tier gate (CASE-463):** runs only in tier-3 repos; if `.claude/kb.json` is absent, skip silently (tier-2 keeps the log local by design). The session stays `active` — this re-mirrors the whole session dir (now carrying the fresh `session-updates.md`) without touching frontmatter; it is Mode 3's mirror minus the `status: closed` flip. Idempotent upsert by `session_id`.

```bash
KB_URL="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')"; KB_KEYFILE="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')"
( cd reports/<SESSION-ID> && python3 -c 'import json,glob; print(json.dumps({"session_id":"<SESSION-ID>","files":{f:open(f).read() for f in sorted(glob.glob("*.md"))}}))' | curl -fsSk -X POST "$KB_URL/apps/kb/server-api/kb/sessions/mirror" -H "X-API-Key: $(cat "$KB_KEYFILE")" -H "Content-Type: application/json" -d @- )
```

If kb is unreachable, log to stderr and proceed — the local append is authoritative; the next mirror-emitting action (another update, wake, or session-end) re-pushes it.

### One session-updates.md per session

Under the session-per-context-window model (CASE-389), `session-updates.md` belongs to a **single** session and grows append-only within it — no multi-session rollover. `/wip-wake` ends the current session and mints a fresh one with its own `reports/<new-id>/` dir, so the next session's running log starts clean. Cross-session continuity is the `continues_from` chain (walk the SESSION records / `CONTINUES_FROM` edges), not in-file `## /resume`-style section breaks. (Legacy mega-sessions like `APP-RC-20260409-1649` predate this and packed many days into one file; new sessions don't.)

### Discipline test

Before writing an entry, ask: *"Would future-me reading this in 6 hours, after a compaction, want to know this?"* If yes, write. If "this is just thinking out loud," don't.

---

## Mode 3 — `/wip-report session-end` (wrap-up)

Closes the session: writes the operator-curated `## Session Summary`, flips the local frontmatter to `status: closed`, and mirrors the closed record to kb. Use before `/clear` or genuine end-of-day — when the next agent reads `session.md` cold. Skip before `/compact`: same agent continues, Mode 2 is the right artifact (see "Picking a mode" at the top).

Three things happen, in order:

1. **Compose the Session Summary** from the current conversation — what happened, dead ends, downstream impact, unfinished, for-the-next-YAC. Operator-curated rich text (not a stock one-liner — that auto-close form is `/wip-wake`'s job). See the YAC Reporting section in CLAUDE.md for the full structure. Reference key `session-updates.md` entries if any were write-worthy in retrospect; the running log may carry minor entries the wrap-up skips.

2. **Atomic local write** — in a *single* read-modify-write of `reports/<SESSION-ID>/session.md` (write a temp file, `mv` over the original — never truncate-in-place), do BOTH: (a) overwrite/insert the `## Session Summary` section in the body, and (b) set frontmatter `status: closed` and `ended_at: <now as a naive datetime, YYYY-MM-DDTHH:MM:SS, NO timezone suffix>`. Collapsing both into one atomic write means a partial failure can't leave a half-state (summary without the status flip, or vice-versa).

3. **Mirror to kb (tier 3 only, warn-and-continue)** — **Tier gate (CASE-463):** kb mirrors run only in tier-3 repos — if `.claude/kb.json` is absent, skip this step silently and continue (tier-2 solo mode is by design; nothing to warn about). Then: `KB_URL="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_app_url"])')"; KB_KEYFILE="$(python3 -c 'import json;print(json.load(open(".claude/kb.json"))["kb_api_key_file"])')"; ( cd reports/<SESSION-ID> && python3 -c 'import json,glob; print(json.dumps({"session_id":"<SESSION-ID>","files":{f:open(f).read() for f in sorted(glob.glob("*.md"))}}))' | curl -fsSk -X POST "$KB_URL/apps/kb/server-api/kb/sessions/mirror" -H "X-API-Key: $(cat "$KB_KEYFILE")" -H "Content-Type: application/json" -d @- )`. The gateway composes the body server-side (session.md first, siblings alphabetical — CASE-464) and upserts by session_id; a closed frontmatter carries `status`/`ended_at` through. If kb is unreachable, log to stderr and proceed — the local write is authoritative; the mirror retries at the next mirror-emitting action or via re-running the same POST.

**Idempotent on an already-closed session.** If the frontmatter already says `status: closed` (you ran `/wip-report session-end` once, or `/wip-wake` auto-closed it), do NOT append a second `## Session Summary` and do NOT re-flip the frontmatter — both are no-ops. Only the kb mirror re-fires (surfaces as `skipped` if the body is unchanged, `updated` if it was edited since).

Confirm to Peter that the summary was written and the session is closed.

---

## Recovery integration (read by `/wip-wake`)

`/wip-wake` reads three files in order to rebuild context:

1. `session.md` — current state (last `/wip-report session-end` snapshot or initial frontmatter)
2. `commits.md` — commits since session start
3. `session-updates.md` — running log (append-only within this session)

The three together rebuild richer context than the previous two-file recovery.
