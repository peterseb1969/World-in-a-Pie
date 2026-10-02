# Namespace Configuration

## Current state

The namespace `clintrial` is hardcoded in 75 places across 29 files. Two central constants cover the server and client reporting paths; the rest are direct string literals in hooks and components.

## Why make it configurable

- Run a second instance against a dev/staging namespace without code changes
- Support multi-tenant deployments where different teams use different namespaces
- Enable backup/restore workflows that target a fresh namespace

## What to change

### Server (1 file, already centralized)

`server/lib/wip-api.ts` defines `const NAMESPACE = 'clintrial'`. Change to:

```ts
const NAMESPACE = process.env.WIP_NAMESPACE || 'clintrial'
```

All server routes use `NAMESPACE` from this module — no other server changes needed.

### Client reporting (1 file, already centralized)

`src/lib/reporting.ts` defines `const NAMESPACE = 'clintrial'`. Change to read from a config endpoint or environment variable injected at build time:

```ts
const NAMESPACE = import.meta.env.VITE_WIP_NAMESPACE || 'clintrial'
```

### Client hooks (~30 files, mechanical)

Hooks that call WIP APIs directly (term management, classification, imports) pass `namespace: 'clintrial'` as a literal. Replace with an import:

```ts
import { NAMESPACE } from '@/lib/config'
```

Files affected (by reference count):
- useClassificationRules.ts (9), useAEAnalytics.ts (7), TAManager.tsx (6)
- useFilteredTrials.ts (5), useTrialDetail.ts (4), useSettings.ts (4)
- useAECleanup.ts (4), AETermManager.tsx (4)
- ~20 more files with 1-2 references each

### SQL table names

Reporting queries reference `doc_ct_trial`, `doc_ct_ta_study__v3`, etc. These are **not** namespace-prefixed in the SQL — WIP's `namespace` parameter sets the PostgreSQL `search_path`, so the same unqualified table names work in any namespace. No SQL changes needed.

### Bootstrap

`server/lib/bootstrap.ts` hardcodes the namespace for template/terminology creation. Same fix — read from `NAMESPACE`.

## Effort

1-2 hours. Mechanical find-and-replace with one architectural decision: whether the client namespace comes from a build-time env var (`VITE_WIP_NAMESPACE`) or a runtime config endpoint. Build-time is simpler; runtime allows switching without rebuilding.

## Risk

Low. The change is purely plumbing — no logic changes, no new features. The main risk is missing a hardcoded reference, which would show up immediately as a 404 or empty result.
