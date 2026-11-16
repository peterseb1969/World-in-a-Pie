---
section: action-required
cases: [1276, 1358]
---

### An app clone picks up the case-claim rule through `CLAUDE.md.refresh` — merge it

A tier-3 clone's `CLAUDE.md` now states the case-claim rule in every profile: acquire a claim
before starting work on a case, tell a holder's refusal apart from a transport error or a refused
key, check the case's claims before picking one, and release when done or stopped. A backend
clone gets it on the next `setup-backend-agent.sh` run — its `CLAUDE.md` is regenerated.

An **app** clone does not. Its `CLAUDE.md` is generated-then-customised, so a re-run of
`scripts/create-app-project.sh` keeps the file as it is and writes the fresh render beside it as
`CLAUDE.md.refresh`. Until that sidecar is merged, an app agent told to fix a case outside
`/wip-case` never sees the rule (`/wip-case` itself carries it: the commands surface is
regenerated).

**Action:** in each tier-3 app clone, re-run `scripts/create-app-project.sh <dir>`, copy the
"Claim a case before you work on it" bullet from `CLAUDE.md.refresh` into the `<!--TIER3-->`
block of `CLAUDE.md`, then delete the sidecar. If the clone's `CLAUDE.md` holds no app-authored
content, pass `--force-claude-md` instead and the render replaces it.
