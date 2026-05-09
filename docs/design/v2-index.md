# v2 Design Index

**Purpose:** Track the design conversations and documents that will inform WIP v2. This is **not** a roadmap. It's an index of structural rethinks surfaced by v1.0 contact with reality.

**Status:** Living document. Add entries as new design conversations happen.

---

## How v2 design is happening

v2 isn't being planned in advance. It's being driven by **v1.0 contact with reality** — the deployment, the three-app stepwise test, the synonym resolution debugging, the install-kit refactor. Each of these surfaced structural inconsistencies that v1.0 either expedient-shipped or inherited from earlier decisions. The pattern is consistent:

1. A v1.0 expedient was cheap and shipped
2. The expedient created an inconsistency the rest of the system kept dancing around
3. Once v1.0 was real, the inconsistency became loud
4. The fix requires acknowledging the design debt as design debt

**Two categories of design documents go in this index:**

- **Forensic** (marked 🔍): written *after* contact with reality. These describe why something hurts, by people who recently suffered. They are concrete and angry in the right way. Capture them while warm — the cost of waiting is that the next agent generation forgets *why* the problem mattered.

- **Speculative** (marked 💭): written *before* contact with reality. These describe what something could be. They're useful for direction but should be re-evaluated against forensic findings before implementation.

---

## Foundation Documents

### 🔍 Pluggable Apps — Greenfield Design
**File:** [`pluggable-apps.md`](pluggable-apps.md)
**Author:** BE-YAC-20260409-1636 (post-Pi-deployment, Day 28)
**Theme:** Apps as managed entities within WIP, not infrastructure peers.

The core insight: every problem hit during the Pi deployment stems from the same root cause — apps are configured at the infrastructure level instead of being managed entities within WIP. The reframe creates an "App Manager" service that pulls images, reads manifests, bootstraps namespaces and data models, provisions per-app API keys, and configures Caddy via its admin API.

**What this eliminates:**
- Per-app Dex client registration (gateway auth: apps read identity from `X-WIP-*` headers)
- The entire CASE-38 saga (5 rounds of OIDC fixes behind Caddy)
- Caddyfile regeneration on app install (dynamic Caddy admin API)
- Manual bootstrap implementations per APP-YAC (manifest-driven)
- Sharing the master API key with all apps (auto-provisioned namespace-scoped keys)
- TLS-between-containers + `NODE_TLS_REJECT_UNAUTHORIZED` hacks (HTTP internal, HTTPS only browser-facing)
- Static passwords in `dex/config.yaml` (Users as MongoDB data, managed via API)
- CASE-39 (password resync), CASE-37 (Express catch-all), most of CASE-38

**Migration path proposed:** v1.0 → v1.5 (App Manager + dynamic Caddy + MongoDB users via Dex connector) → v2.0 (gateway auth, hot install/remove, Console app store) → v3.0 (marketplace, federated IdP support).

---

### 🔍 Template ID Management — v2 Breaking Change
**File:** [`../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-template-id-management.md`](../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-template-id-management.md)
**Author:** BE-YAC + Peter (Day 29 fireside, after the synonym resolution work)
**Theme:** Templates are the only entity in WIP where multiple versions share one canonical ID. Every other entity follows "new version → new ID." The CASE-40/41 synonym resolution work put a spotlight on the inconsistency.

**Core decision:** Separate logical identity (`(namespace, template_value)`) from version handle (`template_id`). Documents reference templates by the logical identity pair by default. Version pinning becomes an explicit, edge-case concern.

**Knock-on changes required:**
- Identity hashing must scope to `(namespace, template_value)`, not `template_id` (post-CASE-36 refactor)
- Reporting tables must key off logical identity, not `template_id` (prevents `ct_trial`, `ct_trial_v2`, `ct_trial_v3` sprawl)
- Replay semantics need per-context decision (validate-against-current vs replay-as-written)

**The Thesis 2 reframe:** Impact analysis on template upgrade isn't a nicety — it's load-bearing for Thesis 2. Agents are particularly bad at schema migration. Without concrete numbers ("4,118 docs affected"), warnings get ignored. The platform owes agents visibility into schema-change consequences. *Without that, Thesis 2 leaks.*

**Pullable to v1.1:** Option 2 (impact analysis) doesn't require the full template ID refactor. Worth pulling forward — every day v1.0 ships without it is a day where any agent can silently corrupt data via schema upgrade.

---

### 🔍 v2 Design Seeds — Collected Themes from v1.1.0
**File:** [`../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-v2-design-seeds.md`](../../FR-YAC/reports/BE-YAC-20260409-1636/fireside-v2-design-seeds.md)
**Author:** BE-YAC + Peter (Day 31, immediately after v1.1.0 release)
**Theme:** Seven architectural questions that v1.1.0 surfaced. Not decisions — seeds. The organising question: *"how do we leverage it?"*

**Seven themes:**

1. **Data integration vs data creation.** ClinTrial/DnD are *lenses* on external data (delete and re-import is fine). AuthorAssist is a *workspace* (deletion is destruction). WIP treats both the same today — namespace `mode` metadata needed to drive different deletion policies, import semantics, and migration tooling.

2. **PostgreSQL: convenience or load-bearing?** Designed as optional, but every non-trivial app depends on it (NL query, full-text search, table views). ILIKE works at demo scale; tsvector/GIN indexes needed for real data. Is "skip PostgreSQL" still a valid deployment?

3. **Namespace isolation vs cross-namespace analytics.** Same trial in `vendor_a` and `vendor_b` has different document IDs, same NCT number — are they the same entity? Reporting tables have a namespace column but table names don't include namespace. Security boundary or organisational one?

4. **Agent guidance: what to do, not just what's available.** Data-model-aware slash commands, context-aware query strategy (SQL vs document queries), workflow commands for import/migration.

5. **Template versioning** (extends the template-id-management fireside). New v1.1.0 finding: the restore engine bypasses template validation entirely — restored docs may not validate against the current template version. Invisible today because we don't check.

6. **Data migration as first-class.** Integration pattern (re-import from source) vs authoring pattern (field-level migration rules, dry-run, incremental). Two different toolkits for the same operation.

7. **Pluggable apps** (extends the pluggable-apps design doc). Gateway auth, app-scoped RBAC, users as data, hot lifecycle.

**v1.2 vs v2 split:** Each theme has an incremental improvement (v1.2) and a breaking change (v2). Peter's instinct: *"start with what the demo exposed. Make the platform smarter so the apps don't have to be."*

---

## Cross-References and Interactions

The three foundation documents interact in non-obvious ways. Future entries should note interactions here.

| Topic | pluggable-apps | template-id-management | v2-design-seeds |
|---|---|---|---|
| **Identity model** | App-scoped namespace grants extend `NamespaceGrant` with `app_scope` | Identity hash scopes to `(namespace, template_value)`, not `template_id` | Cross-namespace: same entity, different IDs, same business key (Theme 3) |
| **Manifest data model** | Apps declare templates in manifest; bootstrapper creates them with `on_conflict=validate` | Template upgrades need impact analysis — the bootstrapper is the natural place to surface it | Apps should declare PostgreSQL dependencies and namespace mode (Theme 1, 2) |
| **Audit trail** | App lifecycle events go in WIP audit | Template version diffs + migration notes go in WIP audit | Integration vs authoring: audit depth differs by namespace mode (Theme 1) |
| **Users** | MongoDB users managed via WIP API | Migration notes are written by users — needs the same identity model | — |
| **PostgreSQL** | — | Reporting tables key off logical identity | Optional → load-bearing: always-on? tsvector indexes? (Theme 2) |
| **Migration** | — | Schema evolution needs impact analysis | Two toolkits: re-import (integration) vs field-level migration (authoring) (Theme 6) |
| **Restore safety** | — | — | Restore bypasses template validation — restored docs may be invalid against current schema (Theme 5) |

The implication: **the App Manager, the schema-migration-impact-analyzer, and the namespace-mode-policy-engine are converging into the same service.** When an app updates and its manifest declares new template versions, the bootstrapper runs the impact analysis automatically, respects the namespace mode (integration: re-import is safe; authoring: require explicit migration plan), and the App Manager surfaces it before the install proceeds.

---

## Related Existing Design Docs

These existed before the v1.0 push and are speculative — re-evaluate them against v1.0 contact findings before implementing.

*Note: 5 of the 7 entries below were deleted during the doc-audit (April 2026) as pre-contact speculative docs that didn't survive Phase 1's default-delete framing. This is itself evidence for the meta-pattern below — speculative pre-contact docs that were cheap to write didn't survive contact with reality. They are retained here as historical record; see the doc archive if you need the content.*

### 💭 ~~[`distributable-app-format.md`](distributable-app-format.md)~~ *(deleted — superseded by pluggable-apps)*
Predates pluggable-apps. The v1.0 install kit (compose chunks + setup-wip.sh) is the *interim* version of this. Pluggable-apps replaces it for v2.

### 💭 ~~[`app-gateway.md`](app-gateway.md)~~ *(deleted — superseded by pluggable-apps gateway-auth)*
Original gateway concept. Pluggable-apps' "gateway authentication" section is the concrete realization.

### 💭 [`authentication-authorization.md`](authentication-authorization.md)
General auth design. Pluggable-apps' "Users as WIP Data" section supersedes parts of this for v1.5+.

### 💭 ~~[`namespace-authorization.md`](namespace-authorization.md)~~ *(deleted — content absorbed into auth-authz and wip-guide)*
The current `NamespaceGrant` model. Pluggable-apps proposes extending it with `app_scope`.

### 💭 ~~[`event-replay.md`](event-replay.md)~~ *(deleted — superseded by backup-restore-redesign approach)*
Replay semantics design. Template-id-management opens a new question: replay-against-current vs replay-as-written. Needs revisiting with that lens.

### 💭 ~~[`template-draft-mode.md`](template-draft-mode.md)~~ *(deleted — impact-analysis gate is the live concern per template-id-management fireside)*
Template lifecycle. Template-id-management adds a new constraint: the upgrade flow needs impact analysis as a gate.

### 💭 [`backup-restore-redesign.md`](backup-restore-redesign.md)
Already implemented in v1.0 (the direct-Mongo cursor approach). Listed here because future v2 work on App Manager backup/restore should reference its lessons.

---

## What's Missing (anticipated)

These topics will likely surface as future forensic design docs as v1.0 sees more use:

1. **External user authentication.** Current v1.0 only supports WIP-managed users. The colleague demo or external deployment will surface "I want to log in with my Google account / SSO." Federation via Dex connectors is sketched in pluggable-apps (Option C) but undocumented.

2. **Multi-tenancy.** Can two instances of the same app run in different namespaces? (e.g., ClinTrial for Roche data in `roche-ct`, ClinTrial for public data in `public-ct`.) Open question #4 in pluggable-apps.

3. **App-to-app communication.** Open question #5 in pluggable-apps. Probably surfaces when the second app needs data from the first.

4. **Container runtime abstraction.** Currently the install kit assumes podman-compose. K8s deployment, Docker Desktop, Nomad, etc. all need different App Manager backends. Open question #1 in pluggable-apps.

5. **Resilient composition.** 🔍 Surfaced during Pi install-kit testing (Day 30): any container should be able to restart, crash, be replaced, or be added, and the system should self-heal without manual intervention. Today's reality: implicit ordering dependencies (Postgres must be up before reporting-sync), passwords baked into volumes on first start that drift from `.env` on re-runs (CASE-39), health checks that mask real failures, no retry/reconnect logic. The v1.0 install kit papers over this with "wait 45 seconds." The v2 App Manager is the real answer (lifecycle ownership, dependency graph, health gates, credential distribution). **v1.1 stepping stone:** compose `depends_on` with `condition: service_healthy`, retry loops in service startup, `setup-wip.sh` detecting existing Postgres volumes and reusing passwords instead of regenerating.

6. **Operational concerns.** Logs, metrics, alerts at the App Manager level. Not yet sketched anywhere.

6. **Permission UX.** RBAC is designed (app-scoped grants in pluggable-apps), but the Console UI for managing it isn't.

7. **Versioning beyond apps.** WIP itself versions. How does an installer know if a v1.5 install kit is compatible with a v1.0 deployment? `requires_wip` is in the manifest spec but the enforcement story isn't.

---

## Conventions

When adding a new entry to this index:

1. **Mark it 🔍 (forensic) or 💭 (speculative).** This affects how it should be read.
2. **Cite the trigger event.** "Written after the Pi deployment" or "Written during the synonym resolution work" — this is what makes the doc concrete.
3. **List what it eliminates / what it costs.** v2 design isn't free. Be explicit about the migration burden.
4. **Cross-reference other entries.** v2 is one design space, not many. Show the interactions.
5. **Note pullable items.** What can ship in v1.1 or v1.2 without the full v2 refactor?

---

## Meta-pattern

The two foundation documents share the same shape:

**A v1.0 hack that was cheap to implement is now load-bearing for v2 design decisions, and the right move is to retire the hack.**

- Apps-as-compose-peers was cheap. Now it's the root cause of the entire CASE-38 saga and the bootstrap fragmentation.
- Templates-with-shared-canonical-IDs was cheap. Now it's the only entity that doesn't fit the synonym resolution model.

Future entries should be evaluated against this pattern. *"Is this a hack we shipped because it was cheap, that's now in the way?"* If yes, it belongs here.
