---
section: fixes
cases: [1397]
---

### The setup script's hooks guard reads git's hook names, and a radon-less re-run says what the hook is really doing

Two corrections to step 4b of `scripts/setup-backend-agent.sh`, which decides
whether `core.hooksPath` points at `scripts/git-hooks`:

- **A stray file in `.git/hooks` blocks the ratchet only if git would run it.**
  The scan checked the executable bit but not the name, so an archived hook —
  `mv .git/hooks/pre-commit .git/hooks/pre-commit.disabled`, or a `cp -p`
  `commit-msg.bak` — held `core.hooksPath` unset while reporting it as a hook
  git runs, which it never did. Entries are now matched against the hook names
  githooks(5) lists (git 2.47: `post-commit`, `post-merge`, `pre-auto-gc` and
  the rest, not only the names git ships samples for) before counting.
- **A run without radon now reports the state it found.** The radon check ran
  before the key was read, so a clone whose earlier run had set
  `core.hooksPath = scripts/git-hooks` was told the hook was NOT enabled while
  it stayed installed and refused every commit. The step now reads the key first
  and says the hook **is** active, with `.venv/bin/pip install radon==6.0.1` to
  clear it and `git config --unset core.hooksPath` to turn the gate off. It
  leaves the key as it found it. The same distinction is made in the test-tool
  install warning.

The CASE-1305 operational note is rewritten to the rules the step applies now.

**Action:** none. A clone held back only by an archived hook picks the ratchet up
on the next setup run; one whose venv lost radon sees why its commits are refused.
