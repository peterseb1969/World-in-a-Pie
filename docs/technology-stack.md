# WIP App Technology Stack — Canonical v1

**Status:** Canonical (CASE-302 propagated). Drafted in `FR-YAC/papers/technology-stack.md`; this is the gene-pool authoritative copy that `create-app-project.sh` propagates into new app trees.
**Authority:** This file is the **canonical statement** of what stack a WIP app uses in v1. The v1 KB spec §3.1 + §13 reference this filename.
**Audience:** APP-YACs at scaffold time; humans reviewing app architecture; FRanC + DOC-YAC for cross-app consistency audits.

---

## 1. Required runtime stack

These are non-negotiable in v1. Apps deviating from this list need an explicit case + Peter approval.

| Layer | Choice | Current version | Rationale |
|---|---|---|---|
| Language | TypeScript | ^5.8 | Type safety, IDE support, ecosystem alignment |
| Build tool | Vite | ^6.x | Fast HMR, ESM-native, tree-shaking; aligned with the React 19 era |
| UI framework | React | ^19.1 | Server components, concurrent rendering, modern hooks |
| Routing | react-router-dom | ^7.x | Declarative routing, basename-aware (`import.meta.env.BASE_URL`) |
| Server state | TanStack Query (`@tanstack/react-query`) | ^5.x | Cache, dedup, retry, refetch — the model the `@wip/react` hooks expect |
| Styling | Tailwind CSS | ^3.4 | Utility-first, theme-extensible, no runtime CSS-in-JS overhead |
| Icons | lucide-react | ^0.46+ | Consistent set, treeshakeable, currently used by all four sibling apps |
| HTTP client | (built into `@wip/client`) | n/a | Apps don't import `axios` / `fetch` directly — see §4 |

### Why React 19, not 18

WIP-Constellations + WIP-AA + APP-KB + APP-RC are on React 19; WIP-ClinTrial + WIP-DnD are still on 18.3. v1 mandates 19 for new apps; existing 18.x apps are grandfathered until next refresh cycle. Don't introduce more 18.x apps.

### Why Tailwind 3, not 4

Tailwind 4 changes config-file conventions (CSS-first). Until the four sibling apps + scaffold migrate together, v1 stays on 3.x. Track upgrade as a future cross-app case.

---

## 2. Required @wip/* libraries

Every WIP app uses some or all of these. They're vendored as `file:libs/wip-*-X.Y.Z.tgz` per CASE-249 + CASE-58 lessons (no npm registry; tarballs live in the app's `libs/` dir or a project-relative path; `.gitignore`d in apps that vendor them).

| Library | Role | Provided by |
|---|---|---|
| `@wip/client` | Typed REST client for all WIP services (registry, def-store, template-store, document-store, reporting-sync) | `World-in-a-Pie/libs/wip-client/` |
| `@wip/react` | TanStack Query hooks wrapping `@wip/client` (`useDocument`, `useTemplate`, `useTriggerBatchSyncAll`, etc.) | `World-in-a-Pie/libs/wip-react/` |
| `@wip/proxy` | Express middleware that forwards `/wip/*` requests to backend with API-key injection (so the browser never sees the key) | `World-in-a-Pie/libs/wip-proxy/` |
| `@wip/auth` | Wip-auth gateway middleware for OIDC + session state (apps with auth flows) | `World-in-a-Pie/libs/wip-auth/` |

**Provider order is fixed:** `<QueryClientProvider><WipProvider>...children</WipProvider></QueryClientProvider>`. Reverse this and `@wip/react` hooks fail to find the client.

**Where to point at:** new apps' `package.json` references should be relative file:// paths (`file:./libs/wip-client-X.Y.Z.tgz` or `file:../libs/...`), never absolute paths to a developer's home dir. Absolute paths are non-portable and have caused CI breakage historically.

**Tarball refresh cadence:** when BE-YAC ships a new `@wip/client` tarball into `World-in-a-Pie/libs/`, individual apps decide when to bump. The tarballs are stable build artifacts; old apps keep their old tarballs until a feature requires the new one. There is no auto-bump.

---

## 3. AskBar / agent server stack (apps with `/api/ask`)

Apps that include the askBar feature (currently: APP-KB, WIP-DnD, WIP-AA — pattern from `scripts/scaffold-query/`) ship a server-side agent that proxies to Claude via the Anthropic SDK + MCP.

| Component | Choice | Notes |
|---|---|---|
| LLM SDK | `@anthropic-ai/sdk` | **REQUIRED ^0.95+ as of 2026-05-08.** Scaffold currently ships ^0.39.0 — outdated; bumped CASE: see §6. |
| MCP transport | `@modelcontextprotocol/sdk/client/{stdio,sse,streamableHttp}` | Three transports supported per `agent.ts`'s factory; the operator picks via env. Streamable HTTP is preferred for remote MCP servers. |
| Default model | `claude-haiku-4-5` (alias, not dated) | Aliases auto-track newest patch. Use the dated form `claude-haiku-4-5-20251001` only when pinning is intentional. |
| Streaming | Off in v1 | Non-streaming `messages.create` is simpler; switch on streaming when UX requires it. |
| Prompt caching | **Required.** | The system prompt + tool definitions are static across all askBar requests in a session — perfect cache candidates. See §6. |

### Discoveries audit (2026-05-08)

The scaffold's `agent.ts` is functionally correct but has known drift from current SDK best practice:

1. **`@anthropic-ai/sdk@^0.39.0` is 56 minor versions behind 0.95.1.** WIP-DnD has bumped locally to 0.80.0. WIP-AA still on 0.39.0. Bumping the scaffold to 0.95+ unblocks newer features.
2. **No prompt caching.** `agent.ts:177` calls `anthropic.messages.create({system: systemPrompt, tools: mcpTools, messages: ...})` — the `system` and `tools` fields are static and would benefit from `cache_control: {type: 'ephemeral'}` markers. Latency + cost win for askBar.
3. **Default model is dated**: `claude-haiku-4-5-20251001` rather than the alias `claude-haiku-4-5`. Aliases let Anthropic patch model behaviour without an app rebuild.
4. **Tool-result content is JSON-stringified (line 222):** `JSON.stringify(result.content)` — works, but feeds the model JSON-as-string when it could feed structured content blocks. Minor.

These drift items are not breaking; they're "could be better." Bumping the scaffold is the gene-pool concern. Existing apps refresh when convenient.

---

## 4. Conventions

### File layout (apps with both client + server)

```
app-name/
├── src/                    # Browser bundle (Vite-built)
│   ├── components/         # Reusable UI components
│   ├── pages/              # Route-level page components
│   ├── lib/                # Helpers, hooks, types
│   └── App.tsx             # Router root
├── server/                 # Node bundle (tsx watch in dev)
│   ├── index.ts            # Express entry
│   ├── agent.ts            # AskBar / MCP agent (apps with /api/ask)
│   └── lib/                # wipApi, sse, bootstrap helpers
├── libs/                   # Vendored @wip/* tarballs (gitignored)
├── docs/                   # App-specific docs (technology-stack.md + ui-guidance.md propagated here)
└── manifest.yaml           # `wip-app.yaml` for wip-deploy
```

### Provider order

```tsx
// src/main.tsx
<QueryClientProvider client={queryClient}>
  <WipProvider client={createWipClient({ baseUrl: '/wip' })}>
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <App />
    </BrowserRouter>
  </WipProvider>
</QueryClientProvider>
```

The `baseUrl: '/wip'` matches `@wip/proxy`'s mount in Express (apps proxy `/wip/api/*` → backend with auth-key injection). The browser never sees the API key.

### Router basename

Always `import.meta.env.BASE_URL`. Vite sets this from `vite.config.ts`'s `base:` field, which `wip-deploy` configures from the manifest. Hardcoding a basename breaks production deploys.

### Express server middleware order (apps with `@wip/proxy`)

```ts
// server/index.ts (excerpt)
app.use('/wip', wipProxy({ baseUrl, apiKey }))      // 1. proxy first (uses raw body)
app.use(express.json())                              // 2. JSON parser AFTER proxy
app.use('/server-api', bootstrapRouter)             // 3. server routes
app.use(staticAssetsRoot, staticHandler)            // 4. static last
```

**Order matters:** `@wip/proxy` reads bodies as raw streams; if `express.json()` consumes the stream first, the proxy returns empty bodies. CASE-299's bootstrap.server.ts.template trips on the same gotcha — keep proxy ahead of JSON parsers.

---

## 5. Forbidden / discouraged choices

### Forbidden in v1

- **No additional state stores** (Redux, Zustand, Jotai, etc.). TanStack Query covers server state; React's `useState` covers component state. Apps with global UI state needs use Context — file a case if that's not enough.
- **No Bootstrap CSS / Bulma / Foundation / etc.** Tailwind only.
- **No styled-components / emotion / CSS-in-JS runtimes** alongside Tailwind. The runtime cost + style-cascade ambiguity is worse than the convenience.
- **No client-side index libraries** (Fuse.js, Lunr, FlexSearch). FTS is a backend concern (reporting-sync's tsvector + the `search` MCP tool); apps query, they don't index.
- **No `axios` / fetch directly to WIP services.** All WIP API access goes through `@wip/client` (browser via the `@wip/proxy`-mounted route, server via direct env). New apps that bypass this re-implement auth, retry, and error-shape handling.
- **No date libraries other than the platform default.** TBD on platform default — currently apps mix `date-fns`, native `Date`, `Intl.DateTimeFormat`. Cross-app consolidation is a future case.

### Discouraged but not blocked

- **react-i18next** is currently in WIP-AA but not other apps. v1 doesn't mandate i18n; if you add it, match WIP-AA's setup.
- **Storybook / chromatic** for component preview — not in any current app. Worth considering for v2; v1 stays pragmatic.

---

## 6. Versioning policy

### When the gene pool bumps the canonical stack

- **Major-version bumps** (React 19→20, Vite 6→7, Tailwind 3→4): cross-app case, tested in one app first, then rolled. Spec §3.1 references this file; bumping happens in this file with a `## Changelog` entry.
- **Minor-version bumps** (e.g. Tailwind 3.4→3.5): scaffold updates; existing apps refresh on next touch. No case required.
- **`@anthropic-ai/sdk` bumps**: track current LTS-ish; bump when feature warrants. Each major minor (0.40, 0.50, 0.60, …) typically breaks little; mostly additive.

### How existing apps reconcile

When this file ships a new mandatory choice (e.g., bumping React 19→20), existing apps:

1. See the change in `World-in-a-Pie/docs/technology-stack.md` (this file).
2. Plan their refresh — usually batched with their next feature work.
3. Run the bump locally; verify against their tests.
4. Push as a normal commit; no cross-repo coordination needed unless `@wip/*` tarballs need rebuilding too.

The gene pool doesn't force-update existing apps. Drift is allowed but bounded — apps should converge within a release cycle (3-6 weeks of constellation time).

---

## 7. Open items at v1 ship time

- The askBar audit (this file's §3) flags four drift items in `scripts/scaffold-query/`. Bumping the scaffold to current SDK + adding prompt caching is a small case (probably one commit + one PR), filed separately as a follow-on to CASE-302.
- The Tailwind theme extension (`tailwind.config.js`'s `theme.extend`) for the canonical color palette belongs in `ui-guidance.md`'s sister file. This file references the stack; that file references the visual.
- Lessons from APP-KB-YAC's bootstrap (CASE-299: PoNIF #4 silent-bulk-error trap; provider-order gotcha) are in `bootstrap.server.ts.template`'s scope, not this file's. Cross-reference only.

---

## Changelog

- **2026-05-08 (v1.0)**: First version. Closes CASE-302's technology-stack-md propagation gap. Authoritative for new apps from APP-KB onward; existing apps grandfathered to refresh on next touch.
