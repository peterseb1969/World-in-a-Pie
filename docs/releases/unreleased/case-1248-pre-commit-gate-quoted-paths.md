---
section: fixes
cases: [1248]
---

### The app pre-commit gate checks paths git would quote; a refresh replaces the old gate

The `.githooks/pre-commit` gate the scaffold installs in every app read the
staged paths with `git diff --cached --name-only`. Under git's default
`core.quotePath` that prints a path with a non-ASCII character, a `"`, a
backslash or a control character C-quoted. The quoted string matched none of
the forbidden-path patterns and, handed back to git, named no file, so a
`.env`, a machine secret or a key under such a directory was committed with no
word. A name starting with `:` slipped through the same way, read as a
pathspec. The gate now reads the paths NUL-delimited and hands each back as
`:(literal)`. The commit logger's "report-only commit" rule had the same
quoting gap and is fixed the same way.

A scaffold refresh replaces a `.githooks/pre-commit` that is still the
previously shipped gate byte for byte, and says so. A hook the app has
edited is kept, and the refresh warns that it may carry the gap.

**Action:** re-run the scaffold on each app clone. If the refresh warns that
`.githooks/pre-commit` differs, make that hook read staged paths
NUL-delimited (`-z`), as the shipped gate does.
