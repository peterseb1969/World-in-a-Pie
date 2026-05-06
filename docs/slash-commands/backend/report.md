Capture session-meaningful work for the Field Reporter.

Three modes. Each writes to a distinct file in your session report dir at `/Users/peter/Development/FR-YAC/reports/<your-session-id>/`. Pick by what you're capturing, not by how you feel.

| Invocation | Mode | File written | Convention |
|---|---|---|---|
| `/report` (no arg) | Fireside | `report-<slug>.md` (one per decision) | Decision artifact: design discussions, choice points, alternatives weighed, rationale |
| `/report update-session [optional terse note]` | Running log | `session-updates.md` (append-only) | Session-meaningful work that is neither a change, end-state, nor decision |
| `/report session-end` | Wrap-up | `session.md` (Session Summary section, overwritten) | End-of-work synthesis: what happened, dead ends, downstream impact, unfinished, for-the-next-YAC |

### Picking a mode before context resets

Two distinct events look like "session ending" but have different recovery semantics. Pick by what comes next:

| Next event | Same agent? | Use this mode | Why |
|---|---|---|---|
| `/compact` | Yes — conversation summarized; agent identity persists | **Mode 2** (running log) | Post-compaction self reads `session-updates.md` and picks up where the pre-compacted self left off. A Session Summary is wasted effort — it'd be re-overwritten next compaction. |
| `/clear` | No — conversation reset; agent re-reads CLAUDE.md cold | **Mode 3** (session-end) | The post-clear agent has no in-conversation memory; the Session Summary is the artifact it starts from. |
| End-of-day / new session | No — next session is a different `<PREFIX>-YYYYMMDD-HHMM` agent | **Mode 3** (session-end) | Same reason — next agent reads cold from `session.md` + `commits.md` + `session-updates.md`. |

Reflex check: if the agent identity persists across the event, you want the running log. If a fresh agent starts from durable artifacts, you want the wrap-up.

### Prerequisites

You must have a session ID and report directory already created (see YAC Reporting section in CLAUDE.md). If you don't have one yet, create it now before proceeding.

---

## Mode 1 — Bare `/report` (fireside)

Use for design decisions worth a permanent record.

### Steps

1. Get the current time:

   ```bash
   date '+%Y-%m-%d %H:%M'
   ```

2. Identify the topic. Infer from context. If unclear, ask Peter. Create a short slug: `namespace-deletion-design`, `mutable-terminologies`, `scope-change-auth`.

3. Create a file at `/Users/peter/Development/FR-YAC/reports/<YOUR-SESSION-ID>/report-<topic-slug>.md` with this structure:

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

4. Tell Peter the report was written and what file it's in. Continue with the session's work.

### When to use Mode 1

- Peter says "let's talk about..." or initiates a design discussion
- A mid-implementation decision changes direction
- Peter corrects an assumption or challenges an approach
- A cross-app or platform-level issue is identified

### When NOT to use Mode 1

- Routine bug fixes → use `commits.md`
- Standard phase work → use `session.md`
- Session-meaningful work without a decision shape → use Mode 2 (`/report update-session`)
- Factual Q&A without broader implications → none of these
- Peter said "off the record" → don't report

---

## Mode 2 — `/report update-session [optional terse note]` (running log)

Use for session-meaningful work that is **neither a change, an end-state, nor a fireside-grade decision**. Three trigger categories:

1. **Discovery without a commit anchor.** Something you learned while reading or exploring that isn't about any specific commit you're about to make. Example: "templates/bootstrap/bootstrap.server.ts.template imports ./wip-api.js and ./lib/sse.js; neither file exists in the scaffold."
2. **Scope-trim decision mid-session.** Why you're doing less than originally pitched, when the rationale isn't architectural enough for a fireside but matters for reading the resulting commit. Example: "Trimmed Step 2 to seed-files-only because the BootstrapGate wiring requires scaffolding that does not yet exist."
3. **Block/unblock state and pre-compaction snapshots.** "Blocked on X waiting for Y." Pre-`/compact` "where I am now" written when context is filling — so the post-compaction same-agent self has more than just the last commit message and a stale session.md. (For `/clear` or end-of-day instead, use Mode 3 — see the "Picking a mode" table at the top.)

**Do NOT use for:**

- Routine "still working" updates — those belong in chat, not the log.
- Change-in-tree — use `commits.md`.
- End-of-session wrap — use `/report session-end`.
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

Append-only running log. Distinct from session.md (overwritten at end) and report-<slug>.md (per-decision). Read by /resume after session.md and commits.md.
```

### Multi-day session rollover

For sessions that span `/resume` calls (multi-day mega-sessions like `APP-RC-20260409-1649`): each `/resume` opens a new section with a `## /resume <YYYY-MM-DD HH:MM>` header. The file grows append-only, but the section breaks let `/resume` readers scope to the most recent block instead of reading through stale context.

Example:

```
## /resume 2026-05-04 09:15
First entry after the resume — picking up where the previous block left off.

## 09:30 — short headline
...
```

### Discipline test

Before writing an entry, ask: *"Would future-me reading this in 6 hours, after a compaction, want to know this?"* If yes, write. If "this is just thinking out loud," don't.

---

## Mode 3 — `/report session-end` (wrap-up)

Overwrites the `## Session Summary` section in `session.md` with: what happened, dead ends, downstream impact, unfinished, for-the-next-YAC. Use before `/clear` or genuine end-of-day — when the next agent reads `session.md` cold. Skip before `/compact`: same agent continues, Mode 2 is the right artifact (see "Picking a mode" at the top). See the YAC Reporting section in CLAUDE.md for the full Session Summary structure.

The end-of-session summary should reference key entries from `session-updates.md` if any were write-worthy in retrospect — but the running log is allowed to carry minor entries that the wrap-up skips.

Confirm to Peter that the summary was written.

---

## Recovery integration (read by `/resume`)

`/resume` reads three files in order to rebuild context:

1. `session.md` — current state (last `/report session-end` snapshot or initial frontmatter)
2. `commits.md` — commits since session start
3. `session-updates.md` — running log (most recent `## /resume` block first, if multi-day)

The three together rebuild richer context than the previous two-file recovery.
