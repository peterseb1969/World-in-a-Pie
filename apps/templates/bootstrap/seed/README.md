# Bootstrap seed stubs

Genesis copies, like the `.template` files beside them: move what you need
into `server/seed/`, then delete `templates/bootstrap/` with the rest. The
loader skips every file whose name starts with `_`, so both stubs below are
inert until you rename them. `bootstrap.server.ts.template` documents the
full seed convention in its header.

## `_namespaces.example.json` → `server/seed/namespaces.json`

The namespace's settings, applied with the namespace upsert on every
bootstrap run. The values shown are the platform defaults, so the file as
shipped changes nothing.

- **Keys:** `description`, `isolation_mode` (`open` | `strict`),
  `allowed_external_refs`, `id_config` (per entity type: `terminologies`,
  `terms`, `templates`, `documents`, `files`, each
  `{"algorithm": "prefixed", "prefix": "DOC-", "pad": 6}` or another
  algorithm), `deletion_mode` (`retain` | `full`). Any other key fails the
  bootstrap before its first write. JSON has no comments; keep notes here.
- **A prefix must be unique on the install.** The Registry's IDs share one
  space across every namespace and entity type, so two namespaces (or two
  entity types) with the same `prefixed` prefix mint the same IDs, and the
  second one's writes fail with a duplicate-key error. Every namespace this
  file is applied to gets the same `id_config`: an app whose dev namespace
  and deployed namespace share an install must not put a literal prefix
  here. Keep the default (UUID7) in that case, and give each entity type
  its own prefix wherever you do use one.
- **`authored_as`** is the namespace the seeds were written in (the dev
  namespace). Replace `TODO_APP_NAMESPACE`, as in the server template.
- **One role keeps the flat seed dirs** (`server/seed/templates/…`). With
  two or more roles each role's seeds move to a role-named subdirectory.
- **Applied only to unused namespaces.** The bootstrap never runs on a
  namespace that holds documents, so editing this file does not change a
  live namespace. Changing a live namespace's ID format or isolation is an
  operator action.
- **`deletion_mode: "full"`** permits hard deletes. The bootstrap confirms
  the flip, so setting it in the seed is the decision.

## `views/_00_example.json` → `server/seed/views/<NN>_<name>.json`

A registered reporting view the app queries: `{name, sql}`. Drop the
leading underscore and give it a numeric prefix to activate it.

- **Unqualified names.** The SQL runs in the namespace's own reporting
  schema: write `doc_<template_value>` (lowercase), never `"<ns>".doc_…` —
  the namespace name differs between dev and deployed installs.
- **Re-registered on every start**, idempotently (same name replaces), and
  again when a query fails because the view is gone.
- **Not in WIP backups.** A restored namespace comes back without its
  views; the app's bootstrap re-registers them.
- **Registration may lag.** A view over `doc_x` registers only after
  reporting-sync has synced template `x`; the startup retry handles that.

## Which key

| Step | Needs |
|---|---|
| Namespace upsert (bootstrap) | an admin key (`wip-admins`): the deployment's key. No namespace-scoped key can create or update a namespace. |
| View registration | namespace **admin** on the app's namespace, or a `wip-admins` / `wip-services` key |

The deployment's key (`WIP_API_KEY_FILE` in dev, `WIP_API_KEY` deployed)
covers both. A least-privilege runtime key that re-registers views at
startup needs `"grant_permission": "admin"`; `write` is not enough.
