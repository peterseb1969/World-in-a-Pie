Roll the current session over into a fresh, linked one and recover context. Use `/wip-wake` after a `/clear`, a compaction reset, the built-in `/resume` of an old transcript, or any time the operator decides "context lost, continuity matters." It closes the prior session (so kb doesn't accrete zombie `active` records), mints a new session ID whose `continues_from` points back at the prior, then reloads context from durable artifacts. (For a brand-new session with no predecessor, use `/wip-setup`.)

### Step A — Roll the session over (deterministic — run the script)

Identity is **local-first**: `.claude/.session-id` is the single source of truth; kb is a derived mirror. Step A is a fully mechanical state machine, so it is executed by a script, not hand-walked step by step. Run it once:

```bash
python3 .claude/scripts/wake-rollover.py
```

It closes the prior session if still active (atomic frontmatter flip + auto-close summary, skipped when already `closed`), mints `<ROLE>-<YYYYMMDD-HHMMSS>` from `.claude/.session-role`, creates `reports/<NEW_ID>/session.md` with `continues_from`, atomically swaps the sentinel, and mirrors both sessions to kb (tier-gated: skipped silently without `.claude/kb.json`; kb-unreachable warns and continues — local writes are authoritative and a re-run converges). It prints the machine-readable contract on stdout:

```
PRIOR_ID=<prior-id>
NEW_ID=<NEW_ID>
PRIOR_SUMMARY=content|stub|absent
```

**Do not re-implement the rollover by hand** — the full state machine (every edge case: missing sentinel, missing prior dir + the legacy shared-path transition note, malformed frontmatter regeneration, collision retry, partial-failure convergence) lives in the script's docstring and its test suite (`agent-scripts/`, `./scripts/wip-test.sh agent-scripts`). On a non-zero exit, read the script's error message: missing sentinel → run `/wip-setup`; missing `reports/<prior-id>/` → resolve per the message (never fabricate state); missing `.session-role` → re-run `scripts/create-app-project.sh <app-dir> --prefix APP-<X>` from the WIP clone (repos whose committed `.app-meta` records `ROLE_PREFIX` self-heal without `--prefix`) (there is NO `--refresh` flag; the scaffolds auto-detect mode).

After Step A, `.claude/.session-id` holds `<NEW_ID>`. Every **write** from here on goes to the new session's dir; the continuity **reads** in Step B target the **prior** session's reports (use the `PRIOR_ID` the script printed).

### Step A.2 — Backfill the prior's summary, if the script says it is a stub

**Skip this step entirely unless the script printed `PRIOR_SUMMARY=stub`.** `content` means someone wrote a real summary — never overwrite it. `absent` means there is no prior, or nothing readable to summarise.

`stub` means the prior's summary is a heading with nothing under it — most often because the prior died without `/wip-report session-end` (a `/clear`, a context wall, a closed laptop) and the close phase you just ran wrote `## Session Summary — auto-closed by /wip-wake (<ts>)` as a placeholder. It also covers a stub an earlier wake left behind. That placeholder is otherwise permanent: the agent who could have written the summary is gone, and nobody runs `session-end` against a session that is not theirs. Now — while the artifacts are still on this disk — is the only moment it gets written.

Reconstruct from what survives, then replace the bare heading in place in `reports/$PRIOR_ID/session.md`, leave `ended_at` alone (the auto-close timestamp is the truth about when the session ended), and re-mirror with `kbc kb-write.py SESSION reports/$PRIOR_ID` (`SESSION` is keyed on `session_id`, so this upserts and cannot mint a duplicate).

The sources, in descending reliability:

- `reports/$PRIOR_ID/commits.md` and `session-updates.md` — the richest, and per-session attributed. The kb mirror bundles them into the SESSION body, so they are readable from kb too, not only on the originating clone.
- Cases the session filed or responded to: `kbc case-fetch.py list --filed-by $PRIOR_ID`. Reliably attributed.
- `git log` over the session's window — **only as corroboration when `commits.md` exists.**

Three ways to get this wrong, each worth avoiding deliberately:

- **Label it as a reconstruction.** A backfilled summary that reads as though the session wrote it fabricates provenance. Say who rebuilt it, when, and from what.
- **Do not build a commit narrative from `git log` alone.** Sessions overlap on the same branch, and `commits.md` is the only per-session commit attribution that exists. Where it is missing, a time-windowed `git log` will hand you another session's commits with full confidence. Cite the case list instead and say plainly that no local commit log survived.
- **When nothing survives, record that.** A session that died early can leave a session dir holding nothing but a few hundred bytes of `session.md` frontmatter — no commit log, no running log. Then write "no durable artifacts survived; reconstructed from kb case activity only", or that nothing was recoverable. Recording irrecoverability is a real artifact. Composing a plausible narrative to fill the space is the fabrication the first rule warns about.

Do **not** hand-close the prior before running the Step A script. `close_prior` returns early on an already-closed session and skips *both* the rewrite and the kb mirror, stranding whatever you wrote on local disk where no peer can read it. Let the script close and mirror; backfill and re-mirror after.

### Step B — Recover context

The rollover is done. Now rebuild working memory from durable artifacts — reading the **prior** session (`<prior-id>`) for continuity, since the new session's dir is still empty.

#### Why this exists

Every long session hits context compaction. Every new session starts cold. Without a defined recovery process, every Claude instance reinvents context recovery — reading random files, guessing at progress, repeating completed work. This command codifies what recovery looks like.

#### Key principle

This command relies ONLY on durable artifacts — files on disk, git history, WIP state. It never assumes anything from a previous conversation. If it's not written down, it doesn't exist.

#### Recovery steps

#### 1. Reload baseline context (mandatory)

Compaction wipes prior reads. As an APP-YAC, you must reload baseline context as concrete tool calls — do not substitute "I remember from training" for actually running the reads. This list is the **same baseline `/wip-setup` forces at creation** — wake must reload it, not a narrower subset, or you recover into a thinner context than you were born with:

- `Read` `docs/Vision.md` — WIP's theses and design principles (bundled into this project by the scaffold, in `docs/`). **This is the drift-correction mechanism**: if any work has bent toward a specific use case at the expense of WIP's generic engine (the failure mode that builds sidecar models instead of using the primitives), this is what catches it. It is exactly the doc a long, feature-pressured session loses sight of — which is why reloading it on every wake is non-negotiable, not just at birth.
- `ReadMcpResourceTool server=wip uri=wip://development-guide` — the four-phase process for building on WIP. **Golden Rule: Never modify WIP. Only consume its APIs.** This is the single most important boundary for an APP-YAC; reload it.
- `ReadMcpResourceTool server=wip uri=wip://ponifs` — the eight PoNIFs (#7 Edge Types and #8 `versioned: false` added 2026-04-25). Conventional assumptions cause silent failures.
- `ReadMcpResourceTool server=wip uri=wip://data-model` — entity shapes in WIP.
- `ReadMcpResourceTool server=wip uri=wip://conventions` — bulk-first 200 OK, PATCH semantics, idempotent bootstrap, template cache, namespace/authorization rules.
- `Read` `docs/wip-deployable-app-contract.md` — what your app must satisfy to ship under `wip-deploy install` (bundled into this project by the scaffold, in `docs/`). Reload it so deploy-contract drift doesn't creep in across a long build.

Output one line per source confirming it was loaded. This step is non-optional; recovery without baseline context is recovery into the same drift the previous session ended in.

#### 2. Check session reports

Read the **prior** session's report dir at `reports/<prior-id>/` (the session you just closed in Step A — that's where the continuity lives; the new session's dir is still empty). Three files together rebuild the session's working memory:

- `session.md` — current state (last `/wip-report session-end` snapshot or initial frontmatter).
- `commits.md` — append-only commit log since session start.
- `session-updates.md` — append-only running log of session-meaningful work that didn't have a commit anchor or fireside (discoveries during reading, scope-trim rationale, pre-compaction snapshots, block/unblock state). Written by `/wip-report update-session`, one file per session.

These are newer than git history (they capture in-progress reasoning that hasn't been committed) and richer than chat (they survived compaction).

#### 3. Check durable documentation
Read the app's documentation files (if they exist):
- `README.md` — what this app does
- `ARCHITECTURE.md` — how it's structured, key decisions
- `WIP_DEPENDENCIES.md` — which WIP entities it uses
- `KNOWN_ISSUES.md` — what's broken or deferred
- `CHANGELOG.md` — what changed recently

If none of these exist, you're likely in early phases (before Phase 4).

#### 4. Check git state
```
git log --oneline -20    # What was committed recently?
git status               # Any uncommitted work?
git diff --stat          # What's changed but not committed?
```

Uncommitted changes are the most fragile state — they survived compaction only because they're on disk, but they haven't been saved to git yet. Note them carefully.

#### 5. Check WIP state
Run the same checks as `/wip-status`:
- `get_wip_status` — are services healthy?
- `list_terminologies` — what vocabularies exist?
- `list_templates` — what document schemas exist?
- `query_by_template(template_value)` for each active template — how many documents?

#### 6. Check seed files
If `data-model/` exists:
- Compare seed files against WIP state
- If they match: Phases 2-3 are complete
- If WIP has entities not in seed files: either Phase 3 was done without export, or work is in progress

#### 7. Determine current phase
Use the evidence to determine where you are:

| Evidence | Phase |
|---|---|
| No terminologies/templates in WIP beyond defaults | Phase 1 (Exploratory) or not started |
| Terminologies exist but no templates | Phase 2 (Design) in progress or Phase 3 (Implementation) started |
| Templates and test documents exist | Phase 3 complete or in progress |
| App scaffold exists in `src/` | Phase 4 (Application Layer) in progress |
| App has multiple committed features, tests pass | Phase 4 complete, now in `/wip-improve` mode |

#### 8. Reconstruct task state
Based on all of the above, determine:
- What phase you're in
- What's been completed (committed work, data in WIP)
- What's in progress (uncommitted changes)
- What's next (the logical next step in the current phase)

#### 9. Report to user
Present a concise recovery summary:

```
Context Recovery Summary:

Phase: 4 (Application Layer) — in progress
Last commit: "feat: add transaction list page" (2 hours ago)
Uncommitted: changes to src/pages/ImportPage.tsx (import flow in progress)

WIP state:
- 5 terminologies (all active)
- 4 templates (all active, v1)
- 127 documents (42 accounts, 85 transactions)

Seed files: present and matching WIP state

Suggested next step: Complete the import flow in ImportPage.tsx, then commit.
```

Ask the user to confirm before proceeding. They may have context you can't recover from artifacts alone.

### When to use this

- **ALWAYS after a compaction — no exceptions.** Run `/wip-wake` the moment context is compacted (manual `/compact`, auto-compaction, or built-in `/resume`), *even when the work feels like a seamless continuation of a long-running plan.* That "seamless continuation" feeling is the trap: it is precisely when the baseline reload (Step B.1) is skipped that an agent drifts — runs on evicted context, loses the vision, and starts taking the cheapest route (e.g. a sidecar model in metadata) instead of using WIP's primitives. The reload is cheap; the drift is not. Do not judge whether you "still remember" — you cannot reliably tell what compaction dropped.
- **At the start of any session** — especially if you're not sure what was done previously
- **When confused** — if something doesn't make sense, recover context before guessing
- **Proactively** — if a session is getting long and you want to checkpoint your understanding

### What this is NOT

This is not a substitute for committing work and writing documentation. If the previous session didn't commit and didn't document, recovery will be incomplete. That's by design — it reinforces the discipline of committing early and often.
