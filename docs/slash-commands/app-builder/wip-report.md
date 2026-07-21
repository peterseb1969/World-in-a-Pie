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
| Context near full (a `/clear` is coming) | No — the next session is a fresh `<PREFIX>-YYYYMMDD-HHMMSS` agent reading cold | **Mode 3** (session-end) | Same reason. The trigger is context pressure, not the clock: a session ends when the window fills, not when the day does. `/clear` is the human's call — your job is to be ready for it (Mode 2 snapshots as context fills), never to propose it on a schedule. |

Reflex check: if the agent identity persists across the event, you want the running log. If a fresh agent starts from durable artifacts, you want the wrap-up.

**A session is bounded by context usage, not by the calendar.** It does not end because a day ended or because the human stopped for the night — a session ID several days old means the context lasted, which is the good outcome, not an orphan. The date in the session ID (`<PREFIX>-YYYYMMDD-HHMMSS`) is a mint timestamp, not a validity range: a session keeps its birth date for its whole life, however many days that is.

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

4. **Mirror the fireside to kb (tier 3 only, warn-and-continue)** — **Tier gate:** runs only in tier-3 repos; if `.claude/kb.json` is absent, skip this step silently (tier-2 solo mode is by design). A fireside is a **first-class, findable FIRESIDE entity** — not session-body sediment. Compose the file locally first (step 3), then:

   ```bash
   kbc kb-write.py FIRESIDE "reports/<YOUR-SESSION-ID>/report-<topic-slug>.md"
   ```

   `kb-write.py`'s fireside extractor maps the frontmatter (`topic`→`title`, `participants`→`authored_by`, `time`→`chat_date`, default `doc_status: published`) and the gateway dedups by `title` (resolve-then-mint — a matching title versions the existing fireside in place), so re-running with the same title is idempotent. If kb is unreachable, log to stderr and **proceed** — the local file is authoritative; re-run the same `kb-write.py` call to retry.

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
3. **Block/unblock state and pre-compaction snapshots.** "Blocked on X waiting for Y." Pre-`/compact` "where I am now" written when context is filling — so the post-compaction same-agent self has more than just the last commit message and a stale session.md. (For an imminent `/clear` instead, use Mode 3 — see the "Picking a mode" table at the top.)

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

After appending the entry, push the running log to kb so its kb copy is never stale — **every** update-session, not a selected subset. **Tier gate:** runs only in tier-3 repos; if `.claude/kb.json` is absent, skip silently (tier-2 keeps the log local by design). The session stays `active` — this re-mirrors the whole session dir (now carrying the fresh `session-updates.md`) without touching frontmatter; it is Mode 3's mirror minus the `status: closed` flip. Idempotent upsert by `session_id`.

```bash
kbc kb-write.py SESSION reports/<SESSION-ID>/session.md
```

If kb is unreachable, log to stderr and proceed — the local append is authoritative; the next mirror-emitting action (another update, wake, or session-end) re-pushes it.

### One session-updates.md per session

Under the session-per-context-window model, `session-updates.md` belongs to a **single** session and grows append-only within it — no multi-session rollover. `/wip-wake` ends the current session and mints a fresh one with its own `reports/<new-id>/` dir, so the next session's running log starts clean. Cross-session continuity is the `continues_from` chain (walk the SESSION records / `CONTINUES_FROM` edges), not in-file `## /resume`-style section breaks. (Legacy mega-sessions like `APP-RC-20260409-1649` predate this and packed many days into one file; new sessions don't.)

### Discipline test

Before writing an entry, ask: *"Would future-me reading this in 6 hours, after a compaction, want to know this?"* If yes, write. If "this is just thinking out loud," don't.

---

## Mode 3 — `/wip-report session-end` (wrap-up)

Closes the session: writes the operator-curated `## Session Summary`, flips the local frontmatter to `status: closed`, and mirrors the closed record to kb. Use before `/clear` — when the next agent reads `session.md` cold. Skip before `/compact`: same agent continues, Mode 2 is the right artifact (see "Picking a mode" at the top).

Three things happen, in order:

1. **Compose the Session Summary** from the current conversation — what happened, dead ends, downstream impact, unfinished, for-the-next-YAC. Operator-curated rich text (not a stock one-liner — that auto-close form is `/wip-wake`'s job). See the YAC Reporting section in CLAUDE.md for the full structure. Reference key `session-updates.md` entries if any were write-worthy in retrospect; the running log may carry minor entries the wrap-up skips.

2. **Atomic local write** — in a *single* read-modify-write of `reports/<SESSION-ID>/session.md` (write a temp file, `mv` over the original — never truncate-in-place), do BOTH: (a) overwrite/insert the `## Session Summary` section in the body, and (b) set frontmatter `status: closed` and `ended_at: <now as a naive datetime, YYYY-MM-DDTHH:MM:SS, NO timezone suffix>`. Collapsing both into one atomic write means a partial failure can't leave a half-state (summary without the status flip, or vice-versa).

3. **Mirror to kb (tier 3 only, warn-and-continue)** — **Tier gate:** kb mirrors run only in tier-3 repos — if `.claude/kb.json` is absent, skip this step silently and continue (tier-2 solo mode is by design; nothing to warn about). Then: `kbc kb-write.py SESSION reports/<SESSION-ID>/session.md`. The client parses `session.md` (frontmatter → SESSION fields, body → the `body` field) and the gateway upserts by `session_id`; a closed frontmatter carries `status`/`ended_at` through. (The whole session dir goes up, not just `session.md`: a file argument pointing at `session.md` deliberately resolves to its parent directory, and every `*.md` sibling — `commits.md`, `session-updates.md`, any `report-*.md` — is bundled into the SESSION body as its own section. So the commit log and running log ARE cross-agent visible in kb; they are recovery files, not local-only ones. Bundling is also what keeps a frontmatter-only `session.md` from being rejected for an empty body.) If kb is unreachable, log to stderr and proceed — the local write is authoritative; the mirror retries at the next mirror-emitting action or via re-running the same `kb-write.py` call.

4. **Mirror this YAC's memory to kb** (same **tier-3 gate** + **warn-and-continue** as step 3) — beside the SESSION mirror, capture the memory this YAC has accrued: `MEMDIR="$HOME/.claude/projects/$(pwd | sed 's#/#-#g')/memory"; [ -d "$MEMDIR" ] && kbc kb-write.py YAC_MEMORY "$MEMDIR"`. The loader writes one record per `memory/*.md`, upserting by `(owner, mem_key = filename stem)` (owner from `.claude/.session-role`), normalizes the two frontmatter shapes, and skips `MEMORY.md`. It is idempotent — unchanged files come back `skipped`, so running it every session-end is delta-only — and **self-skips if the instance has no `YAC_MEMORY` type yet**, so it is safe to land before every instance has the schema. If kb is unreachable, log to stderr and proceed: best-effort, never blocks the close. This makes the YAC's memory cross-agent-visible in kb, the same way `session.md` already is.

**Idempotent on an already-closed session — with one exception.** If the frontmatter says `status: closed` and the `## Session Summary` section **has content**, do NOT append a second one and do NOT re-flip the frontmatter — both are no-ops. If instead the body ends at a bare `## Session Summary — auto-closed by /wip-wake (<ts>)` line with nothing under it, that is a placeholder, not a summary: **replace it in place** with the real one, leave `ended_at` alone (the auto-close timestamp is the truth about when the session ended), and re-mirror. Never overwrite a summary a human or an agent actually wrote.

The distinction matters because the two states look identical to a `status: closed` check but are opposites: one is a finished record, the other is the absence of one standing where it should be. Treating the placeholder as sacred is what makes empty summaries permanent. Either way the kb mirror re-fires (surfaces as `skipped` if the body is unchanged, `updated` if it was edited since).

Confirm to Peter that the summary was written and the session is closed.

---

## Recovery integration (read by `/wip-wake`)

`/wip-wake` reads three files in order to rebuild context:

1. `session.md` — current state (last `/wip-report session-end` snapshot or initial frontmatter)
2. `commits.md` — commits since session start
3. `session-updates.md` — running log (append-only within this session)

The three together rebuild richer context than the previous two-file recovery.
