# Build-App Playbook (Phase 4)

Full procedure for the `/build-app` slash command. The slash command stub at `.claude/commands/build-app.md` contains the prerequisite gate and the build-incrementally warning. By the time you are reading this, Phase 3 is complete and the data model is verified.

## Before Writing Any Code

1. Run `/wip-status` to confirm all terminologies and templates from Phase 3 are intact.
2. Read `docs/cards/build.md` — the pinned stack, providers, proxy, `baseUrl`, UI and bootstrap rules, and the client-side consequences of PoNIF #3 (document identity) and PoNIF #4 (bulk-first 200 OK).
3. Open the depth documents when the card does not answer a question: `docs/WIP_DevGuardrails.md` (all seven guides), the @wip/client and @wip/react READMEs, `docs/WIP_PoNIFs.md`.
4. Confirm the app name, gateway path, and internal port with the user.

## UX Proposal — GATE (requires user approval)

Before writing any component code, propose the UI plan to the user. This is a product decision, not a technical one — the user must approve it, just like the data model in Phase 2.

Present a concise plan covering:
- **Page structure:** What pages/views does the app have? What is the landing page?
- **Navigation:** Sidebar, tabs, top nav? How does the user move between sections?
- **Primary workflows:** What does the user do most? Import data? Browse transactions? View summaries?
- **Key screens:** For each page, describe what the user sees — table, cards, charts, forms?
- **Data entry:** How does data get in? File upload, manual form, paste, API sync?
- **Mobile considerations:** Which pages must work on phone-width screens?

Example format:

```
Statement Manager — UI Plan

Pages:
- Accounts: list of accounts as cards (type, institution, balance, currency)
- Transactions: filterable table (date range, account, category, type, search)
- Payslips: monthly timeline, click to expand line item detail
- Import: upload CSV/PDF, preview parsed data, map columns, confirm

Navigation: sidebar with Accounts / Transactions / Payslips / Import sections
Landing page: Transactions (most frequently used)
Top bar: app name, home link to portal, breadcrumbs
```

**STOP and wait for user approval.** Do not scaffold or write component code until the user approves the UI plan. The user may want a completely different page structure, navigation pattern, or primary workflow than what you propose.

This gate exists because UX decisions are invisible in the data model but determine whether the app is actually usable. A technically correct app with wrong UX decisions wastes the user's time and the context window.

## Steps

### Step 1: Scaffold the app
Use the app skeleton from Guide 3 of the guardrails:
- Create folder structure (src/, tests/, etc.)
- Create `app-manifest.json` for gateway registration
- Create `.env.example` with WIP connection variables
- Set up `vite.config.ts` with configurable base path
- Set up `tailwind.config.ts` with shared design tokens from Guide 2
- Install dependencies: the pinned stack in `docs/technology-stack.md` (react, the vendored @wip/client and @wip/react, @tanstack/react-query, tailwind) — no component library
- Create the WipProvider + QueryClientProvider setup in App.tsx

### Step 2: Build core pages
For each major feature of the app:
- Use `@wip/react` hooks for data fetching (useDocuments, useTemplate, useTerminology)
- Use `@wip/react` mutations for data creation (useCreateDocument)
- Handle errors using the WipError hierarchy — map to user-facing messages per Guide 6
- Build components from the Tailwind shapes in `docs/ui-guidance.md`, using the design tokens
- Ensure responsive layout (works at 375px width and up)

### Step 3: Build data entry forms
Follow Guide 5 (Data Entry Patterns):
- For simple forms: use `useFormSchema()` to auto-generate from template
- For specialized UIs (e.g., receipt scanning): build custom forms using @wip/client types
- Term fields -> searchable dropdown populated from `useTerms(terminologyId)` (`useTerminology` returns the terminology itself, not its terms)
- Reference fields -> search input using `resolveReference(client, …)` (a standalone `@wip/client` export)
- File fields -> upload zone using `useUploadFile()`, then link FILE-XXXXXX to document immediately (never leave files unlinked — they become orphans)
- Always handle: required field validation, term resolution errors, reference resolution errors

### Step 3b: Build import flows (if applicable)
If the app imports files (CSV, PDF, XLSX):

**Before writing any parser code:**
1. Take a real sample file from the user
2. Run the extraction library against it (e.g., papaparse for CSV, pdf-parse for PDF)
3. Print the raw output to the terminal
4. Examine it carefully — real data has glued fields, missing delimiters, unexpected encodings

**Only then** write the parser, mapping raw extracted fields to WIP template fields. Document every mapping in IMPORT_FORMATS.md as you go, not after the fact.

### Step 4: Build list/table views
- Use `useDocuments({ template_value, namespace, page, page_size })` for paginated document lists, and `useQueryDocuments(query)` when filtering on field values
- Provide filtering by key fields (date ranges, categories, etc.)
- Show loading states, empty states, and error states

### Step 5: Create Dockerfile
Multi-stage build per Guide 3:
- Stage 1: `node:24-slim` — install deps, `vite build`
- Stage 2: `node:24-slim` — the Express server (via `tsx`) serves the API and the built `dist/`; install `wget` for the deployer's healthcheck probe
- The scaffold's `Dockerfile` already does this; keep both stages on the same base
- Expose the app's internal port

### Step 6: Write tests
Per Guide 7 (Testing Contract):
- Data layer tests against live WIP (create, version, validate, reference)
- UI component tests (form renders, validation, error display)
- At least one E2E flow (create -> view -> update)
- Health endpoint returns 200

### Step 7: Verify definition of done
- [ ] All data layer tests pass
- [ ] All UI component tests pass
- [ ] At least one E2E flow works
- [ ] app-manifest.json is valid
- [ ] Health endpoint returns 200
- [ ] All documentation files present and current (see Step 8)

### Step 8: Document the app

Run `/document` to generate all required documentation files:
- README.md — what the app does, how to run it
- ARCHITECTURE.md — page structure, component hierarchy, data flow, key decisions
- WIP_DEPENDENCIES.md — terminologies, templates, cross-app references
- IMPORT_FORMATS.md — supported data formats with column mappings (if applicable)
- KNOWN_ISSUES.md — what's incomplete or intentionally deferred
- CHANGELOG.md — initial entry

Commit:
```
git add apps/{app-name}/*.md
git commit -m "docs: initial documentation for {app-name}"
```

**An undocumented app is an unmaintainable app.** The next session starts cold. Documentation is the app's memory.

## After Phase 4: Iterative Improvement

Once the app passes the definition of done, is documented, and is committed, switch to the `/improve` command for all subsequent work on this app. The `/improve` protocol has different rules than Phase 4 — focused on surgical fixes, not greenfield building.

## Reminders
- All runtime data goes through WIP via @wip/client. No local storage.
- Use the prescribed tech stack. No substitutions.
- Follow the design tokens (colours, typography, spacing) from Guide 2.
- Navigation: top bar with app name, home link to portal, breadcrumbs.
- No custom authentication UI — auth is handled by WIP/gateway.
- If you need to check WIP's current state during development, you can still use MCP tools for quick queries — but the application code itself must use @wip/client.
