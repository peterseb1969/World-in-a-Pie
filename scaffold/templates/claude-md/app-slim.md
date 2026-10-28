# __APP_NAME__

> __APP_NAME__ — TODO: replace this line with what this app does.

You build this app ON TOP of World In a Pie (WIP). This file is short on purpose: the rules live in
`docs/cards/`, and scripts do the bookkeeping. Read only what a step below names.

## Every wake

Read `docs/cards/core.md` on every `/wip-setup` and `/wip-wake`. It holds the rules that prevent a
wrong action: what an app on WIP is, the model rule, the approval gate, the eight PoNIFs.

## Dev Namespace

Your development namespace is `__DEV_NAMESPACE__`. MCP calls run with an admin key that spans
namespaces: pass `namespace="__DEV_NAMESPACE__"` on every call. A missing namespace at session start
is normal — it is created when the model is implemented, not as a setup step.

## Phases and gates

| Command | Does | Card | Gate |
|---|---|---|---|
| `/wip-explore` | Phase 1: inventory WIP, understand the domain | core + model | — |
| `/wip-design-model` | Phase 2: map the domain onto WIP primitives | model | the user approves the model explicitly |
| `/wip-implement` | Phase 3: create the approved model via MCP, verify with test documents | model | approved model only |
| `/wip-build-app` | Phase 4: build the React app | build | the user approves the UX proposal |
| `/wip-improve` | After Phase 4: one issue per session | build (model if the change touches the data model) | a data-model change goes back to Phase 2 |
| `/wip-deploy` | Redeploy this app into the dev install | ship | — |

Each phase command records the phase itself (`.claude/scripts/set-phase.py`); never edit
`.claude/.app-phase` by hand. Also available: `/wip-export-model`, `/wip-document`, `/wip-lesson`.

## Session protocol

- **Start:** `/wip-setup` for a fresh session, `/wip-wake` to continue one.
- **Commit:** end every commit message with trailers, one per line:
  `Tests: <X passed, Y failed — or "not run">`, and when there is something to say,
  `PoNIF: <which, and whether it caused issues>` and `Discovered: <surprises, bugs, gaps>`.
  The `post-commit` hook writes the `commits.md` entry from git and these trailers.
- **Stop:** `/wip-report session-end` before Peter runs `/clear`.
- **Never hand-write an ID or a timestamp.** Session IDs, times and commit metadata come from the
  scripts, `date` and git. Your session ID is in `.claude/.session-id`.
- **A compaction is not a new session.** The re-anchor hook prints the core card and your phase;
  re-read them and continue in the same session. Do not run `/wip-wake` after a compaction.
- **Never commit or push without Peter's explicit go-ahead.**
<!--TIER3-->
- **Cases:** `/wip-case file|list|read|respond|comment|close|implement` — all KB reads and writes go
  through the served client (`kbc case-fetch.py …`, `kbc kb-write.py …`), never a raw curl.
<!--/TIER3-->

## Stop rule — 300k tokens

`.claude-context-pct` holds your context usage, e.g. `42% (220k/524k tokens)`; the status line turns
red at 300k tokens used. From 300k on, start no new work: finish or park the current step, run
`/wip-report session-end`, and ask Peter for `/clear`. Do not wait for a compaction.

## App-specific gotchas

<!-- Add the traps this app has hit, one line each, as you learn them. -->

## Where things are

| Need | Read |
|---|---|
| The rules, every wake | `docs/cards/core.md` |
| Data model (Phases 2–3) | `docs/cards/model.md`, then `wip://data-model`, `wip://conventions` |
| Building the app (Phase 4) | `docs/cards/build.md`, then `docs/WIP_DevGuardrails.md`, `docs/technology-stack.md`, `docs/ui-guidance.md` |
| Deploying | `docs/cards/ship.md`, then `docs/wip-deployable-app-contract.md`, `docs/playbooks/wip-deploy-app.md` |
| A PoNIF in depth | `docs/WIP_PoNIFs.md` |
| Why WIP is this way | `docs/Vision.md` |
| Client libraries | `libs/wip-client-README.md`, `libs/wip-react-README.md`, `libs/wip-proxy-README.md` |
| The app's runtime key | `WIP_API_KEY_FILE` in `.env` (the wip-deploy secrets file) |

Open a depth document only when a card does not answer the question.
