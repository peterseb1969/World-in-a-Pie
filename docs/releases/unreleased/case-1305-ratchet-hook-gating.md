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
  for life over a skipped install. The hook is now not enabled and the reason is
  printed, with the command to fix it. CI's lint job runs the same check either
  way. Where an earlier run already enabled it, the script says the hook **is**
  active and refuses every commit until radon is installed, and names
  `git config --unset core.hooksPath` as the way to turn it off.
- **the hook file absent or not executable** — `core.hooksPath` is left unset.
- **`.git/hooks` already holds hooks git would run** — setting the key stops git
  looking there at all, which retired those hooks in silence. A file there counts
  as a live hook only when git would run it: one of git's hook names
  (githooks(5)), and executable. Such a hook may be a regular file or a link to
  a script elsewhere, and the directory is searched through even when
  `.git/hooks` itself is a link. Backups left next to the real hooks
  (`pre-commit.disabled`, `commit-msg.bak` — `mv` and `cp -p` keep the mode bit)
  and non-executable files are not hooks and no longer hold the ratchet back.
  The script lists what it found and leaves the key alone; chain
  `scripts/git-hooks/pre-commit` from those hooks to add the ratchet.
- **a re-run with the key already at `scripts/git-hooks`** — hooks added to
  `.git/hooks` since then are not run, because git looks only at
  `core.hooksPath`; the script prints a `WARNING:` naming them.

**Action:** none required. A clone that expected the ratchet to run before each
commit and sees one of these messages has no local gate — run
`.venv/bin/python scripts/check-complexity-ratchet.py --base HEAD~1 --head HEAD`
yourself, or set `core.hooksPath` once the blocking hooks are gone. The one
message meaning the opposite is the radon one on a re-run: there the gate is
live, and either `.venv/bin/pip install radon==6.0.1` or
`git config --unset core.hooksPath` clears it.
