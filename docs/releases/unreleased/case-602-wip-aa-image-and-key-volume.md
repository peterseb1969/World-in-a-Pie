---
section: deployment
cases: [602]
---

### AuthorAssist pins a published image and keeps a key set in Settings across restarts

`apps/wip-aa/wip-app.yaml` pinned `20260610a`, a tag that was never built, so
an image-based (compose or k8s) install of AuthorAssist could not pull it. It
now pins `20261108b`, the current multi-arch image on ghcr.io. Dev installs
that build AA from `--app-source` are unaffected by the tag.

An Anthropic key set in AA's Settings page was held in memory only and lost at
the next container restart. AA now has a small `settings` volume at `/data`
and `ANTHROPIC_API_KEY_FILE=/data/anthropic-key`. The key the Settings page
saves is written there and read back at start, ahead of the
`anthropic-api-key` secret, which stays as the fallback.

**Action (k8s only):** the volume makes AA a StatefulSet instead of a
Deployment. Roll it with a full `wip-deploy redeploy --name <install>`, whose
prune removes the old Deployment. A scoped `app-deploy wip-aa` does not prune,
and would leave the old Deployment running beside the new StatefulSet behind
one Service.
