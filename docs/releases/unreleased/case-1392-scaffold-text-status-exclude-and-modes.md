---
section: fixes
cases: [1392, 1393]
---

### The query preset and wake-rollover carry no case numbers, the status line never stops on its git exclude, and a refresh never widens a file's mode

- The query preset's files (Dockerfiles, `.env.example`, server and UI
  sources) and the vendored `wake-rollover.py` explain themselves without
  case numbers, which a new app has no KB to look up. A test now checks
  every generated surface for them. Apps already scaffolded keep their
  copies: the query preset's files are the app's own once written.
- The status line's `.git/info/exclude` bookkeeping is best-effort. An
  exclude file that cannot be written (a shared git dir another user owns)
  or is not text no longer stops the context file or the activity record.
- A scaffold refresh keeps an existing file's mode. The one bit it sets is
  the owner's execute bit on a hook, so a hook that lost it still runs.
  An owner-only 0600 hook an earlier engine left used to become 0711,
  executable by others who cannot read it. It now becomes 0700.

**Action:** files an engine before this change wrote are owner-only (0600,
hooks 0700). If another user or a container running as another uid must
read them, widen them once by hand, e.g. `chmod -R go+rX .claude docs
CLAUDE.md`. A refresh will not do it.
