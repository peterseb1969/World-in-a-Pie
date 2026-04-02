
## Auth Phase 1 — Gateway Authentication for Apps (2026-03-31)

- `c002b8e` Add TrustedHeaderProvider to wip-auth for gateway authentication
- `16b84a3` Add forwardIdentity option to @wip/proxy for gateway auth headers
- `7bdf46e` Add wip-apps Dex client and OIDC env vars to scaffold
- `d9f4900` Add OIDC auth middleware to query scaffold
- `48d9824` Update roadmap: Auth Phase 1 complete
- `57629b5` Fix roadmap: mutable terminologies Console UI done, add auth design doc to table
- `7a055b7` Rewrite auth design doc to match actual Phase 1 implementation
- `4e5abd1` Split roadmap: move completed items to docs/completed-features.md
- `90acb0f` Add roadmap item: zero-friction app scaffold dev setup
- `870af84` Fix @wip/client: resolve relative baseUrl in browser environments
- `be971bd` Fix scaffold: set NODE_TLS_REJECT_UNAUTHORIZED=0 in dev server script
- `10533f3` Add dev namespace workflow to app scaffold
- `84a4b12` Fix standard preset: add reporting and files modules
- `2234afd` Add image-based distribution design and roadmap updates
- `48e0098` Add wip-toolkit seed command for /export-model seed files
- `ce15d0b` Fix scaffold: defer env reads to avoid ESM dotenv race

## Auth Phase 2 — Namespace Permission Enforcement (2026-04-01)

- `0e548f3` Add Auth Phase 2: namespace permission checks on user-facing endpoints
- `cac5055` Verify Auth Phase 3: audit trail identity chain confirmed end-to-end
- `e56cf75` Reject documents with undeclared fields instead of silently storing them
- `16259de` Fix reporting-sync: stop retry storm on missing templates, track tables_managed
