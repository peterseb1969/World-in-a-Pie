# Bootstrap seed stubs

Genesis copies, like the `.template` files beside them: move what you need
into `server/seed/`, then delete `templates/bootstrap/` with the rest. The
loader skips every file whose name starts with `_`, so both stubs below are
inert until you rename them. `bootstrap.server.ts.template` documents the
full seed convention in its header.

## `_namespaces.example.json` → `server/seed/namespaces.json`

The app's namespace roles, for an app that owns more than one namespace or
whose seeds reference another namespace. A single-namespace app needs no
file.

- **`authored_as`** is the namespace the seeds were written in. Replace
  `TODO_APP_NAMESPACE`, as in the server template. Cross-namespace refs in
  the seeds (`"<authored_as>:VALUE"`) are rewritten to each role's
  configured name.
- **`env`** (any role but `main`) names the env var that holds the role's
  namespace on this install.
- **`external`** lists namespaces the seeds may reference without owning
  them. `wip` is always allowed.
- **One role keeps the flat seed dirs** (`server/seed/templates/…`). With
  two or more roles each role's seeds move to a role-named subdirectory.
- **No namespace settings here.** A namespace's settings
  (`isolation_mode`, `allowed_external_refs`, `id_config`, `deletion_mode`,
  `description`) are declared in the app manifest, under `spec.namespaces`
  in `wip-app.yaml`, where the operator approves them and the install
  reserves the namespace for the app. A role that still carries `"config"`
  fails the bootstrap before its first write. The server template's header
  documents `spec.namespaces`.

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

The app's own key, with `admin` on its namespaces, runs the whole
bootstrap:

| Step | Needs |
|---|---|
| Namespace upsert | `admin` on a namespace the install reserves for the app (`spec.namespaces`); the namespace is created exactly as reserved. A platform admin key (`wip-admins`) also works. |
| Terminologies, templates, the bootstrap record | `write` on the namespace |
| View registration | `admin` on the namespace, or a `wip-admins` / `wip-services` key |

A `write`-only key can neither create the namespace nor re-register views
at startup. Until the install gives each app its own namespace-scoped key,
apps run with the deployment's key, a platform admin key, which covers every
step.
