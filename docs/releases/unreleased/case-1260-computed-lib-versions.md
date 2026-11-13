---
section: operational
cases: [1260]
packages: [wip-client, wip-react, wip-proxy, wip-archive, wip-toolkit]
---

### Library versions are computed; the scaffold builds what apps vendor

The five packages apps vendor (`@wip/client`, `@wip/react`, `@wip/proxy`,
wip-archive, WIP-Toolkit) now ship under versions computed from git:
`<base major>.<base minor>.<base patch + N>`, where N counts the commits to
the package since its base was written (`scripts/lib_artifacts.py version`).
A fix no longer edits a version, and the scaffold builds all five fresh on
every run; tarballs and `dist/` are no longer in git.

- **Apps:** the next scaffold refresh vendors the libraries under their
  computed versions (for example `wip-react-0.26.15.tgz`) and syncs
  `package.json` and `package-lock.json` as before. Commit both after the
  refresh. Nothing else changes.
- **Building an app scaffold needs npm** on the host. Without it the scaffold
  stops before touching the app, where it used to fall back to the tracked
  tarballs.
- **A build from library changes not yet on develop** (uncommitted, or on any
  other branch, pushed or not) carries a `+dirty.<hash>` or `+local.<tree>`
  suffix, so it never takes a number develop gives to other content. Two
  pushed lane branches that each changed a library used to compute the same
  plain number for different bytes.
- **At a release,** `scripts/release-notes.py cut X.Y.Z` writes each computed
  version as its package's base.

**Action:** none.
