---
section: action-required
cases: [1260]
packages: [wip-auth, wip-deploy]
---

### Platform components carry the release number; the release cut defines every version

The services (Registry, Def-Store, Template-Store, Document-Store,
Reporting-Sync, Ingest-Gateway), auth-gateway, the MCP server, wip-auth and
wip-deploy now all carry one version: the release they were last released in.
Their hand-set numbers (0.1.0 to 0.4.0, wip-auth 0.8.0, wip-deploy 2.0.0a0) are
replaced by 2.1.0, the last release. The OpenAPI `info.version`, each
service's root payload and `wip-deploy --version` report it. A running
service still names its exact build in its status payload (`build.sha`,
`build.image_tag`).

`scripts/release-notes.py cut X.Y.Z` now defines every version a release
ships. It sets X.Y.Z in every platform component and writes each vendored
library's computed version as its base. It refuses, and writes nothing, while
any library holds content not yet on develop. CI refuses a tree whose platform
components disagree (`scripts/lib_artifacts.py platform`).

A library build from a branch other than develop, even a pushed one, now
carries a `+local.<tree>` suffix. Two pushed lanes that each changed a library
used to get the same plain version for different content.

**Action:** anything that compares a service's reported version against the
old numbers (for example `0.4.0` from the Registry) sees `2.1.0` now. The
`schemas/*.json` snapshots keep the old `info.version` until
`scripts/update-schemas.sh` next refreshes them.
