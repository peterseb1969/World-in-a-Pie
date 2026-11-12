# Unreleased changes — one file per entry

Every change on `develop` that the next release note must carry adds **one new
file** here. Nobody edits a shared release file: parallel lanes then never touch
the same file, and release notes stop conflicting on rebase. CI
(`scripts/release-notes.py check`) refuses a malformed fragment and refuses a
recreated `docs/releases/unreleased.md`.

## A fragment

`case-<n>-<short-slug>.md`. Two entries on one case are two files.

```markdown
---
section: fixes
cases: [1260]
packages: [wip-client]
---

### What changed, as a heading

The entry, written as the release note should read it: what changed, who is
affected, and the **Action:** an operator or app must take, if any.
```

| key | | values |
|---|---|---|
| `section` | required | `action-required`, `new`, `fixes`, `first-start`, `deployment`, `operational` |
| `cases` | optional | case numbers |
| `packages` | optional | `wip-client`, `wip-react`, `wip-proxy`, `wip-archive`, `wip-toolkit`: the shipped packages the change touches |

The body is one or more `###` entries; no `#` or `##` headings outside code
fences (the section heading comes from `section`).

## Reading and releasing

- `scripts/release-notes.py preview` prints the whole unreleased note
  (`--out FILE` to write it). Do not commit the rendering.
- `scripts/release-notes.py cut X.Y.Z` writes `docs/releases/vX.Y.Z.md`, grouped
  by section in file-name order, deletes the fragments, and lists the packages
  they name. Curate the written note before committing it.
- Then `scripts/lib_artifacts.py freeze` writes each library's computed version
  (`scripts/lib_artifacts.py version`) as its new base, so the release keeps
  the numbers apps already have. Library versions are never edited for a fix.

The `000-pre-fragments-*.md` files are the old `unreleased.md`, frozen at the
cut-over, one per section it had.
