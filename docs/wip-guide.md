# WIP Guide

The operator-facing guide for installing, securing, and running World In a Pie. If you are deploying WIP, configuring auth or networking, packaging an app to run alongside it, or hardening a production install — this is your one stop.

> **Audience.** You have a target host with Podman (or Kubernetes) installed and want WIP up and useful. You are comfortable with the shell, podman/docker, and editing config files. You do not need to read the codebase to follow this guide. Host-prep advice (Pi SSD setup, macOS Podman machine sizing, cloud VM specs) lives in the [single-host quickstart's Host prep appendix](deploy/podman/README.md#appendix-host-prep-pi-ssd-podman-sizing).

For the *why* — design philosophy, theses, use cases — see [Vision](Vision.md) and the FAQ section in the [README](../README.md). For the data model and APIs, see [api-conventions.md](api-conventions.md), [data-models.md](data-models.md), and the MCP `wip://` resources.

---

## 1. Quick Start

> **Prefer a guided, validated walkthrough?** Pick your target:
> **[single-host — podman + GHCR images](deploy/podman/README.md)** · **[hot-reload dev — local source](deploy/dev/README.md)** · **[Kubernetes](deploy/k8s/README.md)**.
> Building an app on WIP → **[APP-YAC setup tutorial](app-yac-tutorial.md)**. The commands below are the condensed operator reference underneath those guides.

### Install (compose)

```bash
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie

# Recommended default. Generates manifests + .env, brings the stack up.
wip-deploy install --preset standard --target compose --hostname wip.local
```

What this gives you:

- The eight WIP services: the four core data services (Registry, Def-Store, Template-Store, Document-Store), Auth-Gateway (sits in front for API-key + JWT validation), Reporting-Sync (PostgreSQL projection), Ingest-Gateway (async bulk via NATS, enabled by preset), and the MCP Server (when included via the preset)
- The supporting infrastructure: MongoDB, PostgreSQL, NATS, Garage, Dex, Caddy
- HTTPS via Caddy on port 8443 (internal/self-signed by default)
- A starter API key written to the secrets backend

After the install completes, the console is at `https://<hostname>:8443/`.

### Verify

```bash
# Health via Caddy
curl -k https://<hostname>:8443/api/registry/health

# Each service direct (when reachable from your host)
curl http://<hostname>:8001/health    # Registry
curl http://<hostname>:8002/health    # Def-Store
curl http://<hostname>:8003/health    # Template-Store
curl http://<hostname>:8004/health    # Document-Store
curl http://<hostname>:8005/health    # Reporting-Sync (when enabled)
curl http://<hostname>:8006/health    # Ingest-Gateway (when enabled)
```

Aggregated check via the toolkit:

```bash
wip-toolkit status
wip-toolkit status --json    # for piping into automation
wip-toolkit status --integrity   # adds the heavy referential-integrity scan
```

### Deployment targets and presets

`wip-deploy install` accepts three targets and five presets.

| Target | Use it for |
|---|---|
| `compose` | production-style local install, podman-compose / docker-compose under the hood (the default) |
| `dev` | hot-reload local development, with optional `--app-source NAME=PATH` to bind-mount one app from a checkout |
| `k8s` | Kubernetes manifests via the same deployer spec |

| Preset | What it includes |
|---|---|
| `headless` | API services only, no UI |
| `core` | + console + API keys |
| `analytics` | + OIDC (Dex) + reporting-sync + Postgres |
| `standard` (default) | + Garage + MCP server — fully exercisable platform |
| `full` | + ingest-gateway (async bulk via NATS JetStream) |

You can amend a preset with `--add <module>` and `--remove <module>`. Apps (e.g., `react-console`, `clintrial`) are enabled per-install with `--app <name>` or, in dev mode, `--app-source <name>=<path>`.

> **Legacy paths.** v1's `scripts/setup.sh` and `scripts/setup-wip.sh` were retired and **deleted**; only `wip-deploy` ships today. Older external docs may still reference them — ignore those references and use `wip-deploy` for all installs.

---

## 2. Deployment Tiers

Three security tiers cover the realistic deployment ranges.

| Tier | Use case | What you get |
|---|---|---|
| **Tier 1 — Home Pi** | Local network, trusted users | Random secrets, self-signed TLS (`--tls internal`), single API key |
| **Tier 2 — Internet-exposed** | Public access, multiple users | Let's Encrypt (`--tls letsencrypt`), per-service / per-app API keys, NATS and MongoDB authentication (per-service users). |
| **Tier 3 — Enterprise** | High-security requirements | External secrets manager, mTLS, enterprise OIDC. WIP is wired for these but does not ship a turnkey enterprise install today. |

This guide focuses on Tier 1 and Tier 2 — the ones you can stand up with the bundled tooling.

### Tier 1: Home Pi

```bash
wip-deploy install \
  --preset standard \
  --target compose \
  --hostname wip-pi.local \
  --tls internal
```

What you should know:

- TLS is self-signed (Caddy generates and rotates the cert in `<data-dir>/caddy/`). Browsers will prompt you to accept the cert once.
- Random secrets are generated and stored in the secrets backend (default file backend at `~/.wip-deploy/<install-name>/secrets/`, mode `0600`, directory mode `0700`).
- A single starter API key is created. You can promote it to per-service keys later (see §4 *Authentication*).

### Tier 2: Internet-exposed

```bash
wip-deploy install \
  --preset standard \
  --target compose \
  --hostname wip.example.com \
  --tls letsencrypt
```

What changes:

- Let's Encrypt issues a real cert via Caddy. You need port 443 reachable from the public internet for the ACME HTTP-01 challenge, and a hostname that resolves on the public internet.
- HSTS and standard hardening headers (nosniff, X-Frame-Options, Referrer-Policy) are included in the rendered Caddy config for letsencrypt installs — `wip-deploy verify --security` confirms them.
- For testing the ACME flow without burning rate limits, use `--tls letsencrypt --acme-staging` (staging cert is *not* trusted by browsers).
- Run `wip-deploy verify --security` and walk the §7 *Security Hardening* checklist before exposing the host.

### Tier 3: Enterprise

WIP's architecture is enterprise-grade in design (OIDC, namespace isolation, audit trails, controlled vocabularies, federation-ready identity), but the productisation is not there yet — there is no commercial support, SLA, or turnkey enterprise installer. Pieces you would extend for an enterprise install:

- **Secrets:** swap the file backend for a `k8s-secret` backend (built in) or wire a Vault/KMS integration (custom).
- **OIDC:** replace Dex with your enterprise IdP (any OIDC-compliant provider works).
- **TLS:** terminate at your edge, run WIP behind it with `--tls external`.
- **HA:** MongoDB replica sets, PostgreSQL replication, multi-instance services. WIP doesn't ship HA today; the architecture allows it.

---

## 3. Network Configuration

Networking is where most operator failures happen. The three callouts below prevent the most expensive ones.

### 3.1 Critical: `--hostname` is the OIDC issuer

> **The hostname and HTTPS port you install with become Dex's issuer URL, and every token's `iss` claim. Choose them once, as the name browsers will actually use.**
>
> `wip-deploy` derives all of a service's token-validation settings from that one value, so they cannot drift apart. Never set them by hand: every apply re-renders them.
>
> | Setting | Rendered as | Example |
> |---|---|---|
> | Dex `issuer:` (rendered `config/dex/config.yaml`) | `https://<hostname>[:<https_port>]/dex` | `https://wip-pi.local:8443/dex` |
> | Services' `WIP_AUTH_JWT_ISSUER_URL` | the same public URL; it is compared against the token's `iss` | `https://wip-pi.local:8443/dex` |
> | Services' `WIP_AUTH_JWT_JWKS_URI` | Dex's internal address, because services fetch signing keys server-to-server | `http://wip-dex:5556/dex/keys` |
> | Services' `WIP_AUTH_JWT_AUDIENCE` | every Dex client the deployment registers: the gateway's, plus each app that declares its own | `wip-gateway,dnd` |
>
> Dex sets a token's `aud` to the client that requested it. That is why the audience is a list: a token minted for an app's own client is as valid as the gateway's. A key endpoint that can't be reached refuses the request with 503; a token with the wrong issuer or audience gets 401.
>
> **To change the hostname**, re-install with the new `--hostname`. The issuer is also baked into the TLS certificate and every registered redirect URI, and tokens minted under the old issuer stop validating.

### 3.2 Critical: Caddy `handle` vs `handle_path`

> **`handle` preserves the request path; `handle_path` strips the matched prefix.** Picking the wrong one produces silent routing errors.
>
> ```caddyfile
> # CORRECT — service receives /api/def-store/terminologies
> handle /api/def-store/* {
>     reverse_proxy wip-def-store:8002
> }
>
> # WRONG — service receives /terminologies, returns 404
> handle_path /api/def-store/* {
>     reverse_proxy wip-def-store:8002
> }
> ```
>
> WIP services mount under `/api/<svc>/` and expect to see the full path. **Use `handle`.**
>
> **The object-store carve-out.** Garage's S3 API serves at the root of its own URL space, so the deployer routes `/garage/<bucket>/<key>` to Garage with `handle_path` to *strip* the `/garage/` prefix, and restores the upstream Host header (SigV4-presigned URLs are signed against the internal endpoint's Host). This is in the deployer's generated config; you generally don't edit it by hand.

### 3.3 Critical: Caddy returns 200 + empty body on unmatched paths

> **An unmatched path under a Caddy server block does not 404 by default — it returns HTTP 200 with an empty body.** This bites health checks and routing diagnosis hard:
>
> - A monitoring script that probes `/health` on a service that isn't actually routed gets `200 + ""` and parses the empty body as "valid JSON, value is null" or similar — the failure is silent.
> - "Service is healthy" can mean "Caddy is reachable, but nothing is handling this path."
>
> Always validate health endpoints by **content**, not just status code. And when a route looks broken, treat empty 200 as a route-miss until proved otherwise.

### 3.4 Four deployment scenarios

| # | Scenario | Console | API | Dex |
|---|---|---|---|---|
| 1 | Localhost + OIDC | `https://localhost:8443/` | via Caddy on `:8443` | `https://localhost:8443/dex` |
| 2 | Localhost − OIDC | `http://localhost:3000/` | direct on `:8001`–`:8006` | n/a |
| 3 | Remote + OIDC | `https://<host>:8443/` | via Caddy on `:8443` | `https://<host>:8443/dex` |
| 4 | Remote − OIDC | `http://<host>:3000/` | direct on `:8001`–`:8006` | n/a |

Choose by what you need. If you want auth, go with 1 or 3 (and pick `--hostname` deliberately — it becomes the OIDC issuer, §3.1). If you want absolute simplicity for local hacking, scenarios 2 and 4 use API keys only and skip Caddy entirely (services bind to their direct ports, console is on `:3000`).

For scenarios 2 and 4 the console's dev server needs proxy rules (Vite `vite.config.ts → server.proxy`) pointing `/api/registry`, `/api/def-store`, etc. at the matching `localhost:800x`.

---

## 4. Authentication

### 4.1 Two layers: API auth vs database auth

WIP has two independent authentication concerns:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Client ──── API auth ────► WIP service ──── DB auth ───► MongoDB    │
│              (this guide)                  (MONGO_URI)               │
└──────────────────────────────────────────────────────────────────────┘
```

- **API auth** — how clients (browser, scripts, other services) prove identity to WIP's REST API. This is what the rest of this section covers.
- **DB auth** — how WIP services connect to MongoDB / PostgreSQL / NATS internally. Configured via connection strings (`MONGO_URI`, etc.); NATS and MongoDB credentials are per-service users the deployer generates and injects (`NATS_USER` / `NATS_PASSWORD`, `MONGO_USER` / `MONGO_PASSWORD`). It is independent of API auth and not affected by anything below.

### 4.2 API auth modes

The `WIP_AUTH_MODE` env var picks the mode. `dual` is recommended.

| Mode | Description | When |
|---|---|---|
| `none` | Anything goes | Local dev only |
| `api_key_only` | Static / runtime API keys | Service-to-service, scripts, simple setups |
| `jwt_only` | OIDC JWTs only | Pure user-facing apps |
| `dual` (recommended) | Accepts both | Mixed: real users *and* service accounts |

API keys are sent as `X-API-Key: <key>`. JWTs are sent as `Authorization: Bearer <token>`.

### 4.3 API keys: config keys vs runtime keys

WIP supports two kinds of API keys; both authenticate identically.

| Aspect | Config keys | Runtime keys |
|---|---|---|
| Defined in | `config/api-keys.json` | MongoDB, via REST API |
| Created by | Declared in the deployment spec (`--api-key`, below) or by editing the file + recreating services | `POST /api/registry/api-keys` |
| Modifiable via API | No (read-only) | Yes |
| Deletable via API | No | Yes |
| Survives a MongoDB wipe/restore | **Yes** — material lives in the spec + secret backend | **No** — key and grants are Mongo rows; the plaintext is unrecoverable |
| Use case | Bootstrap keys, service accounts, agent keys that must survive redeploys | App keys, temporary keys, UI-created keys |
| `source` field on the key | `"config"` | `"runtime"` |

**Spec-declared keys (recommended for standing agent/service keys).**
Declare keys on the deployment and wip-deploy provisions everything:

```bash
wip-deploy install --target dev \
  --api-key '{"name": "web-yac", "namespaces": ["library", "kb"], "grants": {"kb": "write"}}'
```

For each declared key the deployer generates a random plaintext into the
install's secret backend (`~/.wip-deploy/<name>/secrets/<key>-api-key`,
stable across re-applies), renders `config/auth/api-keys.json` (mode
0600), and mounts it into every backend service. `namespaces` is the
key's read scope; `grants` gives per-namespace `read`/`write`/`admin`
and must stay within `namespaces`. Crucially, a spec-declared key's
grants resolve **locally in wip-auth** — not via Registry grants in
MongoDB — so the key keeps working, at full declared scope, after any
Mongo rebuild (redeploy with a fresh volume, `nuke --remove-data`, or
the wipe-and-restore remediation ritual). Runtime keys die in exactly
those events, and backups do not cover them. The declaration persists
in the install's state: plain `redeploy`/`rebuild` keep it.

Several keys are more comfortably declared in a file than in repeated
`--api-key` JSON — reviewable and diffable deployer input:

```bash
wip-deploy install --api-keys-file keys.yaml
# keys.yaml: a list under a top-level `keys:` (or a bare list) —
# each entry {name, namespaces, grants, owner?, groups?}.
# File entries merge with any --api-key values.
```

Rotate a declared key in place with `wip-deploy rotate-key <name>`: the
key's secret is regenerated, the spec re-applied — restarting the
services that read the key — and the fresh plaintext printed exactly
once. No grace window — the old plaintext stops working at apply;
external holders must be re-handed it. (Runtime keys can't rotate in
place — revoke + create. All key kinds: §7.2.)

### 4.4 Runtime API keys: CRUD endpoints

```bash
# Create
curl -k -X POST https://<host>:8443/api/registry/api-keys \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-app",
    "owner": "peter",
    "namespaces": ["production"],
    "description": "My app"
  }'
```

The response contains `plaintext_key` exactly **once**. Save it then. WIP stores only a bcrypt hash; lose the plaintext and you must revoke and recreate.

```bash
# List (config + runtime), no plaintext exposed
curl -k -H "X-API-Key: <admin-key>" https://<host>:8443/api/registry/api-keys

# Get a single key
curl -k -H "X-API-Key: <admin-key>" https://<host>:8443/api/registry/api-keys/my-app

# Update metadata (PATCH; only fields you include change)
curl -k -X PATCH https://<host>:8443/api/registry/api-keys/my-app \
  -H "X-API-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"groups": ["wip-users"], "enabled": false}'

# Revoke
curl -k -X DELETE https://<host>:8443/api/registry/api-keys/my-app \
  -H "X-API-Key: <admin-key>"
```

Other services discover key changes via the Registry's `/api/registry/api-keys/sync` endpoint, polled every 30 seconds. After creating or revoking a key, allow up to 30 seconds for non-Registry services to pick up the change.

### 4.5 Namespace scoping and privileged groups

API keys carry a `namespaces` field that scopes their access. The rule is:

- **Privileged groups** — `wip-admins` and `wip-services` — are exempt from namespace scoping. A key in either group has access across all namespaces.
- **Every other key** must declare `namespaces` explicitly. Non-privileged keys without a namespace scope receive 403 on every request.
- **Single-namespace keys** get a convenience: omit the `namespace` query parameter and the server derives it from the key's scope. Multi-namespace keys must always pass `namespace` explicitly.

### 4.6 External identity providers (Google, Okta, …)

Dex federates logins from external providers through its *connectors*. They
are spec configuration, like the local users in §4.7: declared once with
`wip-deploy connector`, rendered into Dex's config on every apply, on every
target.

**Add a provider.** Google, as a generic OIDC provider:

```bash
wip-deploy connector add google --type oidc --display-name "Google" \
  --config issuer=https://accounts.google.com \
  --config clientID=<client-id>.apps.googleusercontent.com \
  --secret-file ./google-client-secret       # or --secret-file - to read stdin
```

- `--config KEY=VALUE` is Dex's own connector schema
  (<https://dexidp.io/docs/connectors/>), passed through. Values are read as
  YAML scalars, so `true` is a boolean and `[a, b]` a list; `--config-file`
  takes a whole mapping.
- **The redirect URI is derived**, not configured: `<issuer>/callback`, e.g.
  `https://<your-hostname>:8443/dex/callback`. The command prints it; register
  exactly that URL with the provider. In Google Cloud Console: a *Web
  application* OAuth client, with **Internal** consent for Workspace-only users.
- **The client secret is yours to supply.** It is stored in the install's
  secret backend as `dex-connector-<id>`, never in the spec and never on the
  command line. The deployer never generates it: an apply with a declared
  connector whose secret is missing stops and says how to supply it. Rotate it
  with `wip-deploy connector set-secret <id> --secret-file …`.
- `--secret-key` names the config key Dex reads the secret from: `clientSecret`
  (the default) for OAuth and OIDC providers, `bindPW` for LDAP, or `none` for
  a connector without a secret.

`wip-deploy connector list` shows the declared connectors, and
`connector remove <id>` removes one together with its secret.

**Groups.** A federated user's groups are whatever their provider sends, with
two rules applied by every service and by the gateway:

- **`wip-admins` and `wip-services` are never granted to a federated login.**
  Administrators are always local users (§4.7). If a provider sends either
  group, it is dropped.
- **Group mappings** grant WIP groups by verified email or by domain, since
  most providers send no groups WIP knows. Per-app group gates (route
  `allowed_groups`) depend on them:

```bash
wip-deploy group-map add --email-domain example.com --group wip-editors
wip-deploy group-map add --email ana@partner.org --group wip-viewers
wip-deploy group-map list
```

A mapping trusts the email the provider asserts, so declare connectors only
for providers you trust to verify it. A token that marks the email unverified
gets no mapped groups.

### 4.7 Local users (Dex static users)

The local-account logins on the Dex screen are **spec configuration**: the
install's `auth.users` list, declared once and rendered on every apply,
the same model as the spec-declared API keys in §4.3. Every install starts
with three:

| Email | Username | Group |
|---|---|---|
| `admin@wip.local` | `admin` | `wip-admins` |
| `editor@wip.local` | `editor` | `wip-editors` |
| `viewer@wip.local` | `viewer` | `wip-viewers` |

Each user's password is generated into the install's secret backend as
`dex-password-<username>` and stays stable across re-applies. On the file
backend that is `~/.wip-deploy/<name>/secrets/dex-password-<username>`; on
k8s it is the `dex-password-<username>` key of the `wip-secrets` Secret.

Add, remove and list users with `wip-deploy user`:

```bash
# Declare a user and apply; the password is printed once and kept in the secret backend
wip-deploy user add e2e --email e2e@wip.local --group wip-editors --name <install>

# Change a user's groups in place; the password is kept
wip-deploy user set-groups e2e --group wip-editors,wip-admins --name <install>

# What's declared, and whether each password secret exists yet
wip-deploy user list --name <install>

# Remove a user: logins stop at apply, and the password secret is deleted
wip-deploy user remove e2e --name <install>
```

- `--group` is the user's authorization group, or a comma-separated list
  of groups: `wip-admins`, `wip-editors`, `wip-viewers`, or groups an app
  defines. Dex puts every group into the login's token.
- `set-groups` replaces the user's whole group set. Tokens issued before
  the apply carry the old groups; a fresh login picks up the new ones.
- The username names the password secret, so it must start with a letter or
  digit and use only letters, digits, `.`, `_` and `-`. Emails must be
  unique on the install, because Dex identifies static users by email.
- There is no password-rotate verb. To give a user a fresh password,
  `remove` it and `add` it again.
- `remove` refuses to delete the last `wip-admins` user, and `set-groups`
  refuses to take `wip-admins` away from it. Add another admin first.
- `user add`, `user set-groups` and `user remove` re-render the install's files from the
  current repo but apply only to Dex: Dex is recreated with the new user
  set, and every other container keeps running.

Don't hand-edit the rendered Dex `config.yaml` to add users: it is output,
and the next render overwrites it. Don't hand-edit `auth.users` in the
install's `deployment.deployer-state` either: `user add` makes the same
change with validation and generates the password in the same apply.

A dedicated user is the clean way to give an automated test suite (e2e,
Playwright) a browser login. The suite's logins stay separate from real
users', the user can be removed without touching anyone else, and the suite
reads its password from the secret file. A write made with the user's own
OIDC token records its email as `created_by`. A write an app proxies with
its own API key records `apikey:<key-name>` instead, unless the proxy
forwards the user's identity and the receiving service trusts proxy headers.

---

## 5. Storage and Backup

### 5.1 Default layout

WIP creates a per-service tree under the data directory:

```
<WIP_DATA_DIR>/
├── mongodb/    # MongoDB data files
├── postgres/   # PostgreSQL data files
├── nats/       # NATS JetStream persistence
├── garage/     # Garage object storage
├── dex/        # Dex SQLite + state
└── caddy/      # TLS certificates + runtime config
```

### 5.2 Choosing where data lives

By default the data directory is `<repo-root>/data/`. Override with:

```bash
# Pass to the deployer at install time
wip-deploy install --preset standard --target compose --hostname wip-pi.local \
  --data-dir /mnt/wip-data
```

`WIP_DATA_DIR` is the underlying env var the deployer writes into `.env`. If you want to relocate after the install, stop services, move the tree, update `.env`, and recreate.

### 5.3 Network storage (NFS / GlusterFS / Ceph)

Mount the remote share at `/mnt/wip-data` (or wherever) and use `--data-dir` exactly as above. NFS works well for backup hosts and shared storage. GlusterFS and Ceph are options if you want HA across nodes; they're more involved than this guide covers — your `fstab` for NFS:

```
nas.local:/exports/wip-data  /mnt/wip-data  nfs  defaults,_netdev  0  0
```

### 5.4 Container user mapping (Dex)

Dex runs as UID 1001 inside the container. With rootless podman, the host UID is mapped through the user namespace; the deployer handles this on a fresh install. If you set up storage manually and Dex won't start ("unable to open database file"), set ownership explicitly:

```bash
podman unshare chown 1001:1001 <WIP_DATA_DIR>/dex
podman restart wip-dex
```

Do **not** use `chmod 777` to "fix" permission errors on auth data.

### 5.5 Backup

The recommended quick backup is a stop-and-tar:

```bash
podman stop -a
tar czf wip-backup-"$(date +%Y%m%d)".tar.gz -C "$WIP_DATA_DIR" .
wip-deploy install --preset <your-preset> --target compose --hostname <your-host>   # restart
```

Hot backups for the heavyweights:

```bash
# MongoDB
podman exec wip-mongodb mongodump --out /data/db/backup
cp -r "$WIP_DATA_DIR/mongodb/backup" "./mongodb-backup-$(date +%Y%m%d)"

# PostgreSQL
podman exec wip-postgres pg_dump -U wip wip_reporting \
  > "wip-reporting-$(date +%Y%m%d).sql"
```

For offsite storage, encrypt the backup:

```bash
tar czf - "$WIP_DATA_DIR" \
  | gpg --symmetric --cipher-algo AES256 \
  > "wip-backup-$(date +%Y%m%d).tar.gz.gpg"
```

### 5.6 Logical export/import — the wip-toolkit CLI

The tar backups above are physical (whole data directory, same platform
version back). For **logical, per-namespace** data management — portable
archives, namespace cloning, scripted/headless workflows — use the
`wip-toolkit` CLI. It is a separate surface from the React Console's
backup/restore (which uses document-store's engine); the CLI is for
headless, scripted, and composable use.

```bash
# Export one namespace to a portable ZIP archive (v3 format;
# multi-namespace archives are supported by the format)
wip-toolkit --proxy export mylab mylab-backup.zip --include-files

# See what an archive contains without touching the instance
wip-toolkit inspect mylab-backup.zip

# Clone into a NEW namespace: fresh mode re-keys every ID and rewrites
# references (documents, files, edges) to the new IDs
wip-toolkit --proxy import mylab-backup.zip --mode fresh \
  --target-namespace mylab-copy

# Both export and import accept --dry-run to preview
```

The full command set: `export`, `import` (fresh/restore modes), `inspect`,
`seed` (bootstrap a namespace from seed files), `status` (§7.4),
`update-document`, `backfill-synonyms`. Each documents its options via
`wip-toolkit <command> --help`.

Scope honesty, so you don't discover limits mid-incident: fresh-mode
import is the verified path for cloning and disaster recovery to a new
namespace. Restore mode (same IDs, original prefix) and full fidelity of
edge-type templates through import have known open defects at the time of
writing — check open cases against the toolkit before relying on either.
There is no point-in-time snapshot (an export captures current state), no
archive merge/diff, and archives do not carry API keys or namespace
grants: a restore brings back data, not access.

---

## 6. Apps on WIP

WIP is a backend; "apps" are user-facing UIs that talk to it. The deployer has first-class support for installing apps alongside the platform.

### 6.1 The App Contract (v2)

Each managed app has a manifest at `apps/<name>/wip-app.yaml` **in the WIP repo** (not in the app's own source tree) declaring everything the deployer needs to wire it in. The deployer discovers manifests by globbing `<wip-root>/apps/*/wip-app.yaml` — placing one in your app's source repo does nothing. Example (abridged from `apps/react-console/wip-app.yaml`):

```yaml
api_version: wip.dev/v1
kind: App
metadata:
  name: react-console
  category: optional

spec:
  image:
    name: react-console
    tag: v1.2.2

  ports:
    - {name: http, container_port: 3011}

  env:
    required:
      - {name: WIP_BASE_URL, source: {from_component: router}}
      - {name: WIP_API_KEY,  source: {from_secret: api-key}}
      - {name: PORT,          source: {literal: "3011"}}
      - {name: APP_BASE_PATH, source: {literal: /apps/rc}}
      - {name: NODE_ENV,      source: {literal: production}}

  routes:
    - {path: /apps/rc, auth_required: true}

  depends_on: [document-store]

  healthcheck:
    endpoint: /apps/rc/health
    probe: auto
    start_period_seconds: 15
```

The deployer uses this manifest to:

1. Add the app's container to the generated compose file (or k8s manifest).
2. Generate a Caddy / Ingress route from `routes[].path` to the container port.
3. Wire required env vars from the right sources (`from_component`, `from_secret`, `literal`).
4. Create a Dex OIDC client when `auth_required: true` is set.

> **Legacy v1 contract.** v1 apps shipped a `docker-compose.app.<name>.yml` chunk with `wip.app.*` labels, picked up by `setup-wip.sh`. v2 manifests are the path forward; v1 chunks may still work in older trees but should not be used for new apps.

### 6.2 Auth shape — pick before you implement

WIP supports three distinct authentication shapes for apps. Pick one at design time; switching later means ripping out scaffolding and rebuilding. Two apps in the current constellation made different picks for non-overlapping reasons, and that's fine — what's not fine is picking the wrong shape by drift instead of by decision.

#### Shape A — Gateway-managed (auth-gateway injects `X-WIP-User`)

The auth-gateway terminates OIDC, validates the JWT, and forwards the request to your app with two headers set: `X-WIP-User` (the verified principal) and `X-WIP-Groups` (the user's groups). Your app reads identity from headers; it does not run an OIDC flow, hold sessions, or manage cookies.

- **Manifest:** `routes[].auth_required: true` on every protected route.
- **App env:** `WIP_API_KEY` (or `WIP_API_KEY_FILE`) for outbound API calls. No OIDC client config, no session secret, no cookie config.
- **In WIP today:** react-console.
- **When to pick:** you need per-user authorization but don't need to customize claims handling, session lifetime, profile pages, or the OIDC flow itself.
- **What you give up:** no control over the auth flow. The gateway's verification logic is what it is.

#### Shape B — App-side OIDC (sharing Dex as IdP)

Your app runs its own OIDC client (`express-session` + `openid-client`, or equivalent) and points it at Dex (WIP's IdP). The gateway passes the request through unauthenticated; the app handles login redirects, sessions, cookies, and identity verification itself.

- **Manifest:** `routes[].auth_required: false` (the gateway hands the request to the app; the app gates access via its own session middleware).
- **App env:** `OIDC_ISSUER` (external Dex URL), `OIDC_INTERNAL_ISSUER` (internal Dex URL for localhost), `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `SESSION_SECRET`. Plus `WIP_API_KEY` for outbound platform calls.
- **In WIP today:** WIP-KB (browser-facing routes).
- **When to pick:** you need custom claims handling, custom session lifetime, an in-app login/profile UX, or integration with non-WIP IdPs you've added to Dex's connectors.
- **What you sign up for:** sessions, cookies, the `trust proxy` dance, OIDC `state` parameter discipline, cookie-path-with-trailing-slash — all the gotchas in §6.3 around sessions exist because Shape B is in play.

#### Shape C — API-key only (no user identity)

The app authenticates as a service, not a user. Requests carry `X-API-Key` (or use the file-backed equivalent). There's no session, no cookie, no user binding — every request is "this service did it."

- **Manifest:** either omit `routes` entirely (the app isn't user-facing), or set `routes[].auth_required: false` on the no-user-identity routes.
- **App env:** `WIP_API_KEY` or `WIP_API_KEY_FILE`. Nothing else auth-related.
- **In WIP today:** WIP-KB's askBar NL-query endpoint (talks to the platform via a privileged API key, independent of the session-based user auth on its UI routes); admin scripts; batch importers.
- **When to pick:** your app doesn't have a per-user model. Admin tools, batch processors, system services.
- **What you give up:** audit trail attribution to a user. Every action looks like the service did it. If "who initiated this" matters later, you'll regret it.

#### Decision matrix

| Question | A — Gateway | B — App-side OIDC | C — API-key only |
|---|:-:|:-:|:-:|
| Does your app need user identity? | yes | yes | no |
| Custom claims / session lifetime / login UX? | no | yes | n/a |
| Integration with non-WIP IdPs (Google, Okta, etc.)? | no | yes | n/a |
| Per-user authorization on routes? | yes | yes | no |
| Will every request look like "the service did it"? | no | no | yes |

#### Failure modes for picking wrong

| Wrong pick | Symptom | Cost to recover |
|---|---|---|
| Shape B when A would have worked | You wired up sessions, cookies, OIDC client, `trust proxy`, the §6.3 gotchas — all for what `X-WIP-User` would have given you for free | Medium: rip out scaffold, switch manifest to `auth_required: true`, replace session middleware with header reads |
| Shape A when you actually need custom claims | You hit a wall when needing to extend the gateway's verification, or wanting non-standard session lifetimes | High: switch to B mid-life; everything Shape A skipped you now have to implement |
| Shape C when you need user identity | Audit logs say "service did it"; can't attribute actions; security review later asks "who" and you can't answer | High: add a session/identity layer onto an app that wasn't designed for one |

#### Combining shapes for different endpoints

An app can run multiple shapes side-by-side; each route in the manifest can declare its own `auth_required` and the app dispatches accordingly. WIP-KB does exactly this: browser-facing routes are Shape B (session cookies, app-side OIDC), while the askBar's `/api/ask` endpoint is Shape C (API key against the platform, no user binding because the question doesn't need one).

If you find yourself wanting to mix, that's fine — but declare each route's intent in the manifest explicitly, and document in your app's README which shape lives where. The shape isn't visible to a reader of `wip-app.yaml` from the outside; making it visible saves the next agent debugging a "why is this route un-protected?" mystery.

#### One pragmatic note

Today's `wip-app.yaml` doesn't carry an explicit `auth_mode` field — Shape A vs B is inferred from `routes[].auth_required` (true → A, false + app-side OIDC env vars → B). That works but it's implicit; a future schema addition may name the shape directly so the deploy-readiness check can verify it. Until then, document the intent in the manifest as a top-level comment so a reader doesn't have to guess.

### 6.3 Gotchas — every one was discovered the hard way

These bit real apps in real deployments. Internalise them before you ship.

**1. Bake `VITE_BASE_PATH` at build time.** Vite resolves asset URLs at build, not run. Forgetting `--build-arg VITE_BASE_PATH=/apps/<slug>/` produces an app whose HTML references `/assets/...` instead of `/apps/<slug>/assets/...`, and the browser shows "Refused to apply style — MIME type text/html" errors. **This is the #1 deployment failure.** The pattern in your Dockerfile:

```dockerfile
ARG VITE_BASE_PATH=/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
RUN npm run build
```

Build with: `podman build --build-arg VITE_BASE_PATH=/apps/<slug>/ -t <image> .`

**2. Internal traffic is HTTP, not HTTPS.** `WIP_BASE_URL` from inside an app container must be `http://wip-caddy:8080`, not `https://wip-caddy:8443`. Caddy's HTTPS listener uses a self-signed cert that Node's fetch / undici rejects even with `NODE_TLS_REJECT_UNAUTHORIZED=0`. The HTTP listener on `:8080` is the right destination for internal hops.

**3. Mount everything under `APP_BASE_PATH`.** Caddy preserves the full path. Your app receives `/apps/<slug>/health`, `/apps/<slug>/auth/callback`, etc. Use:

```typescript
const BASE_PATH = process.env.APP_BASE_PATH || ''
const router = express.Router()
router.get('/health', ...)
router.get('/auth/callback', ...)
app.use(BASE_PATH, router)
```

Do **not** rely on Caddy stripping the prefix. Do **not** hardcode paths.

**4. Sessions behind Caddy.** If your app uses sessions for OIDC:

- `app.set('trust proxy', 1)` — without this Express sees HTTP, not HTTPS, and `Secure` cookies don't go back.
- Cookie path = `<APP_BASE_PATH>/` with trailing slash.
- Use a unique cookie name (e.g., `rc.sid`) so apps on the same host don't collide.

**5. OIDC `state` parameter.** Always include a `state` parameter in the auth request. Dex returns it; `openid-client` / `oauth4webapi` will reject the response without it. Use the library's built-in `authorizationUrl()` rather than constructing the URL manually.

**6. Build args, runtime args, and where each lives.**

| Variable | When | How |
|---|---|---|
| `VITE_BASE_PATH` | Build time | `--build-arg` in Dockerfile |
| `APP_BASE_PATH` | Runtime | env from manifest |
| `WIP_BASE_URL` | Runtime | env from manifest (`from_component: router`) |
| `WIP_API_KEY` | Runtime | env from manifest (`from_secret: api-key`) |
| `OIDC_ISSUER` | Runtime | env (the *external* URL — `https://<hostname>:8443/dex`) |

### 6.4 Common app failures

| Symptom | Probable cause | Fix |
|---|---|---|
| 502 on API calls from the app | TLS error proxying to `https://wip-caddy:8443` | Use `http://wip-caddy:8080` |
| 401 on API calls | `WIP_API_KEY` mismatch / stale | Recreate containers so all read the current secret |
| 404 on static assets ("MIME type text/html" errors) | `VITE_BASE_PATH` not baked at build | Add `ARG`/`ENV` to Dockerfile, rebuild with `--build-arg` |
| OIDC "unexpected state" | `state` parameter missing | Use the OIDC library's `authorizationUrl()` |
| OIDC redirect to wrong app | Callback URL missing base path | Mount auth routes under `APP_BASE_PATH`; do not strip prefix |
| Session lost after Dex redirect | `trust proxy` not set, or wrong cookie path | `app.set('trust proxy', 1)`; cookie path = `<BASE>/` with trailing slash |
| `${VAR}` literal in container | Env var not resolved | Verify the manifest's `env.required` lists the var with a real `source:` |

---

## 7. Security Hardening

### 7.1 Encryption at rest

Recommendation: **host-level full-disk encryption**. It's the best balance of security, performance, and simplicity.

- **Linux / Pi** — LUKS:
  ```bash
  sudo cryptsetup luksFormat /dev/sda2
  sudo cryptsetup open /dev/sda2 wip-data
  sudo mkfs.ext4 /dev/mapper/wip-data
  sudo mount /dev/mapper/wip-data /mnt/wip-data
  ```
- **macOS** — turn on FileVault (System Settings → Privacy & Security → FileVault).
- **Windows / WSL2** — turn on BitLocker for the system drive.

If host-level encryption isn't possible, the alternatives are weaker but available:

- **Object-store SSE** — server-side encryption for object storage (store-dependent; Garage encrypts via its own layout or disk-level encryption).
- **MongoDB Community** does not support encryption at rest. Either upgrade to Enterprise or rely on host-level encryption.
- **PostgreSQL** has no built-in at-rest encryption. `pgcrypto` does column-level encryption inside the database, with the usual key-management trade-offs.

Encrypt **backups** before they leave the host:

```bash
tar czf - "$WIP_DATA_DIR" \
  | gpg --symmetric --cipher-algo AES256 \
  > backup.tar.gz.gpg
```

### 7.2 Key rotation

Services read their secrets and key files **once, at start**. A rotated key
reaches them only when they restart, and a plain restart is not enough on
every target (a compose `restart` keeps the old environment; on k8s a
`subPath`-mounted file never changes inside a running pod). `wip-deploy`
handles this: every service's rendered definition carries a hash of the
secrets and key files it reads, so a rotation re-applies the install and
restarts exactly the services whose key changed — k8s rolls their pods,
compose recreates their containers, dev recreates everything. Storage
services (MongoDB, PostgreSQL, NATS, Garage, Dex) are not restarted this way.

**Three kinds of API key, three procedures:**

| Key | Where it lives | Rotate with |
|---|---|---|
| The install's **master key** (`secrets/api-key`), read by every service and app | install secret | `wip-deploy rotate-key --secret api-key --name <install>` |
| **Declared keys** (`auth.api_keys`, e.g. agent keys) | install secret + rendered `api-keys.json` | `wip-deploy rotate-key <key-name> --name <install>` |
| **Runtime keys** (console / `POST /api/registry/api-keys`) | Registry (MongoDB, bcrypt hash) | revoke + create a new one; other services see the change within the 30 s key sync |

`rotate-key` prints the new plaintext **once**, names the services the apply
restarted, and then probes the install's public URL: the Registry must
accept the new key and refuse the old one, or the command fails (`--no-check`
skips the probe). There is no grace window — once the apply completes, the
old value gets 401.

After rotating:

- Clients that read the key **file** need no config change, but only some
  re-read it while running: `kbc` reads it on every call and follows at once;
  an MCP server configured with `WIP_API_KEY_FILE` reads it once at start, so
  open MCP sessions get 401 until they are reconnected (e.g. `/mcp` in Claude
  Code); app servers using `apiKeyFile` follow on their next restart.
- Anyone holding a **copy** of the key (scripts, other machines, people)
  must be given the new value.
- For zero downtime with external consumers that cannot switch at once,
  declare a second key, apply, move consumers over, then remove the first.

**Not rotatable with `rotate-key`:** MongoDB / PostgreSQL passwords, the
Garage keys and Dex secrets. These also need a change in the store that
uses them (the database keeps its own copy of the password), which
`rotate-key` does not make, so it refuses them. The NATS and MongoDB user
passwords (`nats-password-<service>`, `mongo-password-<client>`) are refused
too. The server and the client read the password only at start (MongoDB
applies a changed one on its next start), and on k8s a changed secret
restarts neither server, so the two would disagree until both restart.

Rotation cadences worth defaulting to:

| Secret | Frequency | Notes |
|---|---|---|
| API keys | Annually, immediately on compromise | Higher frequency in regulated environments |
| Database passwords | Annually | Coordinate with maintenance window |
| Dex client secret | Annually | Affects active sessions |
| TLS (Let's Encrypt) | Automatic | Caddy renews 30 days before expiry |

### 7.3 Validate before exposing

Run the automated pre-exposure checklist against the install:

```bash
wip-deploy verify --security --name <name>
```

Read-only. It checks secret file permissions (dir 700, files 600), API-key
strength (not the documented dev default, not trivially short), TLS mode vs
hostname sanity, variant vs exposure (a public-shaped install must run
`--variant prod` so the in-service startup guards are armed), published host
ports (no datastore/admin ports bypassing Caddy), and security headers on
public installs. Exit 0 = safe to expose; each failure prints a fix hint.

Add `--live` to also test the running install through its public URL:

```bash
wip-deploy verify --security --live --name <name>
```

- Every protected route must refuse a request without credentials, and
  health probes must answer without one.
- reporting-sync must run on its unprivileged PostgreSQL roles.
- The install's **canary** key must be refused by each service's privileged
  and namespace-scoped routes. The deployer adds the canary to every
  install as `wip-canary`, with its secret `wip-canary-api-key` beside the
  master key. It authenticates but is authorized for nothing: no groups,
  no grants, scoped to a namespace that is never created. A 401 to the
  canary counts as a failure, because it means a service does not know the
  key. The admin key sends the same requests and must pass them, which
  proves each route exists.

The live checks write nothing. Write routes are probed with an invalid
body, and the permission check answers before validation. A check that
fails after a platform roll means that build does not enforce what the
release claims.

### 7.4 Ongoing health monitoring

Every WIP service exposes `/health`, `/metrics`, and `/health/integrity`, but in a non-techie deployment nobody is watching them. `wip-toolkit status` aggregates these and exits non-zero on anomalies — wire it to cron + email and the install will tell you when something is wrong:

```cron
*/5 * * * *  /usr/local/bin/wip-toolkit --proxy --host wip.local status --quiet --json | mail -E -s "WIP status alert" you@example.com
```

`mail -E` only sends mail when there is non-empty output, and `--quiet` only prints when overall status is not `ok`. Result: silent unless broken.

For a daily integrity scan (heavier — orphaned terminology / template / term references):

```cron
17 4 * * *  /usr/local/bin/wip-toolkit --proxy --host wip.local status --integrity --quiet --json | mail -E -s "WIP integrity alert" you@example.com
```

| Default threshold | Value | Severity |
|---|---|---|
| `events_failed >= N` | 1 | warning |
| `consumer_lag >= N` | 100 | warning |
| `consumer_lag >= N` | 1000 | critical |
| `integrity_drift >= N` | 1 | critical |

Override with `--failed-events-warning`, `--consumer-lag-warning`, `--consumer-lag-critical`.

---

## 8. MCP Server Setup (AI-assisted development)

WIP ships an MCP server exposing **108 tools and 5 resources** to AI coding assistants. An AI agent can discover templates, query documents, manage terminologies, and import data through tool calls — without reading WIP source code. This section is the operator-side setup; for *what the tools do*, see `docs/mcp-server.md` and the `wip://` resources.

### 8.1 Prerequisites

- WIP services running (you should be able to `curl http://localhost:8001/api/registry/health`)
- Python venv with MCP deps (created by `setup-backend-agent.sh` or your own setup)
- An API key in `.env`

### 8.2 Three transport options

`scripts/setup-backend-agent.sh` configures `.mcp.json` for whichever transport you want.

**Local stdio** — most common, AI agent runs on the same machine as WIP:

```bash
./scripts/setup-backend-agent.sh
```

**SSH proxy** — AI agent on your laptop, WIP on a remote host:

```bash
./scripts/setup-backend-agent.sh --target ssh --host your-wip-host.local
```

This requires SSH key-based auth (no password prompts) and a usable `~/.ssh/config` entry. The MCP server runs on the remote host; SSH stdio handles the transport.

**HTTP transport** — network-exposed MCP, for clients that don't speak SSH stdio or for multi-client setups:

```bash
./scripts/setup-backend-agent.sh --target http --host your-wip-host.local
```

The HTTP listener defaults to `0.0.0.0:8000` (FastMCP default) and is gated by an `API_KEY` env var. If you start it without one, the server runs unauthenticated and prints a warning — fine for a private LAN, dangerous on a public network.

### 8.3 Pointing at the right backend

If your agent is calling WIP through Caddy (the standard deployment), set:

```bash
export WIP_API_URL=https://localhost:8443
export WIP_VERIFY_TLS=false   # only when using a self-signed cert in dev
```

To bypass Caddy and target services directly:

```bash
export REGISTRY_URL=http://your-wip-host.local:8001
export DEF_STORE_URL=http://your-wip-host.local:8002
export TEMPLATE_STORE_URL=http://your-wip-host.local:8003
export DOCUMENT_STORE_URL=http://your-wip-host.local:8004
export REPORTING_SYNC_URL=http://your-wip-host.local:8005
```

### 8.4 SSH connection drops

If using SSH stdio and the connection drops:

- Add `ServerAliveInterval 60` to the host's `~/.ssh/config` entry.
- On the remote (Linux), enable lingering for the user (`loginctl enable-linger`) so the session survives logout.

> **Cloud AI + your data.** WIP stores data locally, but if you use a cloud AI in any role that reads or queries that data, the data leaves your machine — through the AI's development context (sample files), MCP queries, or conversational queries. The fix is a local model speaking the same MCP protocol; until those are capable enough for multi-tool reasoning, treat the trade-off as a conscious choice. See README's "Cloud AI" caution for the full breakdown.

---

## 9. Common Failure Modes — Fast Diagnosis

| Symptom | Where to look | Likely fix |
|---|---|---|
| 401 after a successful Dex login | The 401's `detail` names the failed check (issuer, audience, expiry); see §3.1 for what each is rendered from | An issuer or audience mismatch means rendered config was hand-edited: `wip-deploy redeploy` |
| 404 on `/api/<svc>/...` calls | §3.2 Caddy `handle` vs `handle_path` | Use `handle`, not `handle_path` |
| Health probe says "200" but the service isn't really up | §3.3 Caddy 200-on-unmatched | Validate by content, not just status code |
| Env change didn't take effect | §7.2 Key rotation | `wip-deploy redeploy --name <install>` (or `podman-compose down && podman-compose up -d`) — a `restart` keeps the old env |
| "unable to open database file" on Dex | §5.4 Container user mapping | `podman unshare chown 1001:1001 <data-dir>/dex` |
| App returns 502 calling WIP from inside its container | §6.3 gotcha 2 | Use `http://wip-caddy:8080`, not `https://wip-caddy:8443` |
| App's static assets return 404 with MIME-type errors | §6.3 gotcha 1 | Bake `VITE_BASE_PATH` at build time with `--build-arg` |
| Non-privileged API key gets 403 on every call | §4.5 Namespace scoping | Add a `namespaces` field; or put the key in `wip-admins` / `wip-services` |
| MongoDB won't start on a network filesystem | §5.3 Network storage | WiredTiger doesn't like some NFS configurations; prefer local storage for MongoDB |

---

## 10. Where to go next

- **API surface** — `docs/api-conventions.md` (bulk-first pattern, BulkResponse, PATCH semantics) and the MCP `wip://conventions` resource.
- **Data model** — `docs/data-models.md` and `wip://data-model`.
- **Powerful Non-Intuitive Features** — `wip://ponifs` (eight behaviours that surprise newcomers).
- **Auditing and history** — `docs/uniqueness-and-identity.md`.
- **AI-assisted development** — `docs/AI-Assisted-Development.md`, `docs/WIP_AppSetup_Guide.md`, `docs/WIP_DevGuardrails.md`.
- **Reasoning / why** — `docs/Vision.md`, `docs/WIP_TwoTheses.md`.
