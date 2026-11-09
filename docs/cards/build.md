<!-- card-sources: what this card condenses; checked by components/mcp-server/tests/test_card_drift.py
docs/technology-stack.md sha256:5590feb0be212b0e reviewed:bb2cdf88
docs/ui-guidance.md sha256:8667dff0c17994c4 reviewed:a66e110f
docs/WIP_DevGuardrails.md sha256:0357beaebcec2918 reviewed:bb2cdf88
libs/wip-client/README.md sha256:51842f209b9a24e6 reviewed:c50094bb
libs/wip-react/README.md sha256:eba9b7ae31c577b0 reviewed:bb2cdf88
libs/wip-proxy/README.md sha256:e63cb4241bccc1e7 reviewed:4bb50871
docs/wip-deployable-app-contract.md sha256:13eb727c53fdeac8 reviewed:c50094bb
docs/playbooks/app-builder/build-app.md sha256:4d59d990ca82e372 reviewed:43aca6a0
docs/playbooks/app-builder/bootstrap.md sha256:6b9cda0da7c6c00d reviewed:06668a40
scaffold/templates/claude-md/app.md sha256:d3a547014063d535 reviewed:c50094bb
-->
# WIP build card — Phase 4: writing the app

## Stack (pinned, v1)
- TypeScript ^5.8 · Vite ^6 · React ^19.1 · react-router-dom ^7 · `@tanstack/react-query` ^5 · Tailwind CSS ^3.4 (not 4) · lucide-react ^0.46+.
- `@wip/client` (typed REST), `@wip/react` (TanStack hooks), `@wip/proxy` (Express, injects the API key server-side).
- Install the vendored `./libs/wip-*.tgz` plus `@tanstack/react-query` (a peer dep you add).
- `package.json` refs stay relative: `file:libs/wip-<lib>-<version>.tgz`. Never an absolute home-dir path; never hand-edit or re-pack a vendored tarball.
- Router basename is always `import.meta.env.BASE_URL`, never hardcoded.

## Forbidden
- Extra state stores (Redux, Zustand, Jotai…): server state = TanStack Query, local = `useState`, global UI = Context.
- Other CSS frameworks (Bootstrap CSS, Bulma, Foundation) and CSS-in-JS runtimes (styled-components, emotion).
- Client-side search indexes (Fuse.js, Lunr, FlexSearch).
- `axios`/raw `fetch` to WIP services — all WIP access goes through `@wip/client`.
- Local storage for runtime data — all runtime data goes through WIP.
- Dark mode, inline hex colours, `framer-motion`, your own auth/login UI.

## Providers
```tsx
<QueryClientProvider client={queryClient}>
  <WipProvider client={createWipClient({ baseUrl: `${import.meta.env.BASE_URL}wip` })}>
    <BrowserRouter basename={import.meta.env.BASE_URL}><App /></BrowserRouter>
  </WipProvider>
</QueryClientProvider>
```
- `QueryClientProvider` outermost, `WipProvider` inside. Both are required; `useWipClient()` throws outside `WipProvider`.
- Create the client once at the root. Behind the proxy pass **no `auth`**: the proxy injects the key.

## `baseUrl` rules
- Browser, behind your `@wip/proxy` mount: `` `${import.meta.env.BASE_URL}wip` `` (apps are served at `/`, so that is `/wip`).
- Node (server, scripts): absolute URL only (`WIP_BASE_URL`, e.g. `https://localhost:8443`); relative or empty throws.
- Every other client fetch (`/api/...`, `/server-api/...`) is prefixed with `import.meta.env.BASE_URL`.
- Per-deployment values (e.g. namespace) come from `GET /api/app-config` at boot, never `VITE_*`; no API key in any `VITE_*` var.

## App origins (`https://<label>.<host>/`)
- Served at the origin's root, no base path. A browser call to WIP's origin has no session there: it fails.
- Keep `WipProvider` (`@wip/react` >= 0.26.0): it renews the session. With `appSession={false}` call `useAppSessionRenewal` yourself; `onSessionLost` runs before its reload.
- `/auth/logout`, `/auth/userinfo`: relative links.

## Proxy (server)
```ts
router.use('/wip', wipProxy({
  baseUrl: process.env.WIP_BASE_URL!,
  apiKey: process.env.WIP_API_KEY,
  apiKeyFile: process.env.WIP_API_KEY_FILE,
  defaultNamespace: process.env.WIP_NAMESPACE,
  onMissingKey: 'unavailable',
}))
router.use(express.json())   // body parsers AFTER the proxy
```
- The proxy streams the raw body: a parser such as `express.json()` before it drains it and the call stalls. Mount the proxy above every parser, app-level ones included.
- Deployed, the app's own key comes as `WIP_API_KEY`; run locally, `.env`'s `WIP_API_KEY_FILE` wins. Point it at `app-<name>-api-key` once the app is installed.
- `onMissingKey: 'unavailable'`: no key → `/wip` answers 503 instead of the app dying on start.
- Key set or rotated while running: `getApiKey: keyFromFile(path)` (or `() => key`) replaces `apiKey`/`apiKeyFile`.
- Your key spans several namespaces: set `defaultNamespace` or pass `namespace`, or value-form template queries return 422.
- File downloads: `GET <mount>/files/:fileId/content` through the proxy; storage URLs never reach the browser.

## TLS in dev
- WIP serves a self-signed cert, which Node `fetch` rejects. Put `NODE_TLS_REJECT_UNAUTHORIZED=0` in the `dev:server` script **only**, never in `start`/production.

## UI rules
- Tokens (`theme.extend.colors`): `primary` (#2B579A, `-light`, `-dark`), `accent`, `success`, `danger`, `surface`, `background`, `text`, `text-muted`. Use classes (`bg-primary`, `text-text-muted`), never hex; add them to `tailwind.config.js` if absent (the query preset ships them). Font: Inter.
- Page: `<main className="mx-auto max-w-6xl px-4 py-6 sm:px-6 lg:px-8">`, `bg-background` page, `bg-surface` cards.
- Titles: h1 `text-2xl font-semibold tracking-tight`, h2 `text-xl`, h3/card `text-lg font-semibold`.
- Radius: `rounded-lg` cards/modals, `rounded-md` buttons/chips, `rounded-full` pills.
- Callouts: `rounded-lg border border-{c}/20 bg-{c}/5 p-4` + `text-{c}` heading. Other shapes: copy from ui-guidance.
- Buttons: primary `bg-primary text-white hover:bg-primary-light`; destructive `bg-danger`.
- Icons: lucide, `h-4 w-4` inline/button, `h-5 w-5` toolbar.
- A11y floor: visible focus ring `focus:outline-none focus:ring-2 focus:ring-primary/40`; `aria-label` on icon-only buttons; modals trap focus and close on Esc; never colour alone (add icon + text); touch targets ≥32px.
- Works at 375px wide.
- End the root layout with `<WipFooter />` from `@wip/react`: not sticky, no colour override, never hand-rolled.
- Charts: no standard yet; propose one before adding a library.

## Bootstrap gate (runtime, at app launch)
- Namespace missing → show an explicit bootstrap offer (the user confirms, or restores via console first). Never auto-bootstrap silently.
- Namespace exists → use it as-is: no schema reconciliation or merge.
- Exists but no `<NS_PREFIX>_BOOTSTRAP_RECORD` and **no documents** → `incomplete`: offer to resume. Has documents → `resume_blocked`: show namespace and doc count, offer nothing.
- The bootstrap writes the namespace-prefixed record (e.g. `KB_BOOTSTRAP_RECORD`) **last**.
- Build from `templates/bootstrap/*.template` (headers are the depth), then delete the dir.
- Seeds: `server/seed/terminologies/<VALUE>.json`, `server/seed/templates/<NN>_<VALUE>.json` (include the record template). Read from `SEED_DIR`, default `<cwd>/server/seed`.
- Namespace settings live in `wip-app.yaml` `spec.namespaces`, not seeds. Namespace `admin` creates a reserved namespace, re-registers views; `write` can't.
- No restore UI in the app; restore is console-initiated.

## DevGuardrails essentials (Guides 1–7)
- G1 Gateway: reach WIP only through Caddy routes (via your proxy), never service ports.
- G3 Skeleton: `<base>/api/health` must not depend on WIP being reachable (the app contract overrides Guide 3 here).
- G4 Client lib: single-item methods/hooks throw on item failure; bulk methods return `BulkResponse` (see PoNIF #4 below). Typed errors: `WipValidationError` (400/422), `WipNotFoundError`, `WipConflictError`, `WipAuthError` (401/403), `WipServerError`, `WipNetworkError`, `WipBulkItemError` (`errorCode`, `index`, `details`).
- G5 Data entry: generate forms with `useFormSchema(templateValue, { namespace })` (term → select via `useTerms`, reference → `resolveReference(client, …)`, file → `useUploadFile`). Files are two-step: upload → link the returned file ID immediately (unlinked files become orphans). Bulk import: preview + column mapping → validate → bulk create → report created/updated/failed per row.
- G6 Errors: 400/422 → inline field errors, keep form. 401/403 → "session expired", keep form. 404 → message + back link. 5xx/network → persistent banner + retry button. Success toast ~3s. Only GETs auto-retry (502/503/504); writes are retried only by the user.
- G7 Testing: data-layer tests vs live WIP in a test namespace (create, re-create → v2, bad term, bad reference, query, file round-trip). Vitest + Testing Library for components; Playwright for ≥1 create→view→update E2E. Done = all pass + health 200 + README.

## PoNIF #3 — identity (client consequences)
- `createDocument` is an upsert: same identity-field values under the same template = same `document_id`, new `version`. Read `is_new`/`version` from the result; don't assume "created".
- Don't check existence before writing or hash identities app-side; use the returned `document_id`. Identity is scoped to one template.
- Never put timestamps or per-run values in identity fields, or every write becomes a new document.
- Update = re-POST with full data, or `useUpdateDocument({ documentId, patch })` (JSON Merge Patch). PATCH on an identity-less template fails with `append_only`, so create a new doc instead. `versioned:false` templates stay at `version: 1`.
- Term/reference identity fields hash the resolved referent: aliases = one identity.

## PoNIF #4 — bulk-first, 200 OK always
- HTTP 200 ≠ success. Single-item calls/hooks throw `WipBulkItemError` (surfaced as `mutation.error`).
- Bulk calls/hooks (`createDocuments`, `useCreateDocuments`, `useUpdateDocuments`…) resolve normally even when items fail. Always loop `results[i].status === 'error'` and read `error`/`error_code`.
- Report both `succeeded` and `failed`; never infer success from the status code or the error count alone.
- Statuses: `created`, `updated`, `unchanged`, `skipped`, `deleted`, `error`.

## When the card is not enough
- `docs/WIP_DevGuardrails.md`, `docs/technology-stack.md`, `docs/ui-guidance.md` (class strings), `docs/WIP_PoNIFs.md`, `libs/wip-{client,react,proxy}-README.md`.
