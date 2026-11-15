---
section: operational
cases: [1305]
---

### The setup script enables the complexity-ratchet hook only when it can run

`scripts/setup-backend-agent.sh` points `core.hooksPath` at
`scripts/git-hooks` so that CI's complexity ratchet runs on each commit. It now
checks first, and says what it decided:

- **radon missing** — a run with `WIP_SKIP_VENV_INSTALL=1`, or one where the
  tool install failed, left the hook enabled anyway. The hook refuses every
  commit when its interpreter cannot import radon, so such a clone was blocked
  for life over a skipped install. The hook is now left disabled and the reason
  printed, with the command to fix it. CI's lint job runs the same check either
  way.
- **the hook file absent or not executable** — `core.hooksPath` is left unset.
- **`.git/hooks` already holds live hooks** — setting the key stops git looking
  there at all, which retired those hooks in silence. A hook is live whether it
  is a regular file or a link to a script elsewhere, and whether or not the
  hooks directory itself is a link. The script now lists what it found and
  leaves the key alone; chain `scripts/git-hooks/pre-commit` from those hooks
  to add the ratchet.

**Action:** none required. A clone that expected the ratchet to run before each
commit and sees one of these messages has no local gate — run
`.venv/bin/python scripts/check-complexity-ratchet.py --base HEAD~1 --head HEAD`
yourself, or set `core.hooksPath` once the blocking hooks are gone.
