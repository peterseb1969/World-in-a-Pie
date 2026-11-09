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
| `analytics` | + OIDC (Dex) + reporting-sync + Postgres + the app edge (§6.5) |
| `standard` (default) | + Garage + MCP server — fully exercisable platform |
| `full` | + ingest-gateway (async bulk via NATS JetStream) |

You can amend a preset with `--add <module>` and `--remove <module>`. Apps (e.g., `react-console`, `clintrial`) are enabled per-install with `--app <name>` or, in dev mode, `--app-source <name>=<path>`.

> **Legacy paths.** v1's `scripts/setup.sh` and `scripts/setup-wip.sh` were retired and **deleted**; only `wip-deploy` ships today. Older external docs may still reference them — ignore those references and use `wip-deploy` for all installs.

### Upgrading to a new tag

The install's saved spec (`~/.wip-deploy/<name>/deployment.deployer-state`) records which image tags it runs. Change it only through the commands below, never by editing the file.

**1. See what runs, and why.** `images` is read-only:

```bash
wip-deploy images --name wip
```

It prints each service's image, and in SOURCE the rule that decided its tag:

| SOURCE | Meaning |
|---|---|
| `pin` | a per-service tag: `--image-tag NAME=TAG`, `add-app --tag` or `app-deploy --tag` |
| `base` | the install-wide `--tag` |
| `manifest` | the tag in the component's or app's manifest (used when no `--tag` is set) |
| `latest` | nothing set a tag |
| `upstream` | an infrastructure image's own version (mongo, postgres, dex…); the install-wide tag never changes it |
| `source` | dev target only: built locally as `<name>:dev`; no tag applies |

**2. Preview the roll.** Add the new tag. Nothing changes yet:

```bash
wip-deploy images --name wip --tag 20261101a
```

The preview is exactly the roll `redeploy` makes with the same flags. Each row says whether it moves or stays, and apps the new tag alone would move are listed as staying on their current images (step 3 explains why). It ends with the command that applies the roll.

**3. Roll the platform.**

```bash
wip-deploy redeploy --name wip --tag 20261101a
```

`redeploy` writes the new tag into the saved spec, re-renders, and recreates only the services whose rendered definition changed. Data volumes and secrets are kept.

**Apps don't move with the platform.** Each enabled app the new tag would otherwise carry is pinned to the image it runs today, and the command names it. To move an app as well, pin it in the same call (`--image-tag react-console=20261101b`). To move one app later on its own, use:

```bash
wip-deploy app-deploy react-console --tag 20261101b --name wip
```

`app-deploy` renders the whole spec but applies only that app, plus any service whose rendered definition changed. To hotfix one platform service on top of a roll, add `--image-tag registry=20261102-fix`.

**Update the deployer first.** `redeploy` renders the *whole* spec from the deployer checkout it runs from, not just the tag. Update that checkout to the release you are rolling to before running it. If you roll to new images while the checkout is old, or update the checkout while keeping old images, the services can expect a configuration the images don't support.

**Dev installs.** On the dev target, every service built from local source runs `<name>:dev` and ignores tags (SOURCE `source`). A tag roll reaches only images pulled from a registry. To take new code, update the source checkout instead.

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
> That is why `wip-deploy install` requires `--hostname` for `--target compose` and `--target k8s`: there is no default to fall back on silently. Only `--target dev` defaults it, to `localhost`. The preview verbs (`validate`, `show-spec`, `render`) still use the placeholder `wip.local` when it is omitted.
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
> Dex sets a token's `aud` to the client that requested it. That is why the audience is a list: a token minted for an app's own client is as valid as the gateway's. A key endpoint that can't be reached refuses the request with 503; a token with the wrong issuer or audience gets 401. Each service re-reads the signing keys every 5 minutes and keeps using its last copy while Dex is unreachable, but for at most an hour: after that, tokens are refused with 503 until Dex answers again. A token naming a key the copy lacks prompts a re-read, at most one fetch every 10 seconds.
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
| 1 | Localhost + OIDC | `https://wip.localhost:8443/` | via Caddy on `:8443` | `https://wip.localhost:8443/dex` |
| 2 | Localhost − OIDC | `http://localhost:3000/` | direct on `:8001`–`:8006` | n/a |
| 3 | Remote + OIDC | `https://<host>:8443/` | via Caddy on `:8443` | `https://<host>:8443/dex` |
| 4 | Remote − OIDC | `http://<host>:3000/` | direct on `:8001`–`:8006` | n/a |

Choose by what you need. If you want auth, go with 1 or 3 (and pick `--hostname` deliberately — it becomes the OIDC issuer, §3.1). If you want absolute simplicity for local hacking, scenarios 2 and 4 use API keys only and skip Caddy entirely (services bind to their direct ports, console is on `:3000`).

For scenarios 2 and 4 the console's dev server needs proxy rules (Vite `vite.config.ts → server.proxy`) pointing `/api/registry`, `/api/def-store`, etc. at the matching `localhost:800x`.

### 3.5 Apps on origins of their own

Every app, deployed or attached, is served at the root of an origin of its own, `https://<label>.<hostname>[:port]/`, with a session that works nowhere else: `wip_app_session`, host-only, naming the app. WIP's own origin serves nothing under `/apps/`; old `/apps/<name>/` URLs answer 404, with no redirect. There is no other mode.

- **Logging in once still covers every app.** The app origin sends the browser through `/auth/app-login` on WIP's origin, which hands it a one-time code; `/auth/handoff` on the app origin turns that into the app's session. Only WIP's origin talks to Dex, so Dex needs nothing per app. WIP's bare host sends the browser to the first app's origin.
- **Logout reaches an app within its session lifetime** (`APP_SESSION_MAX_AGE`, 15 minutes by default). An app built on `@wip/react` 0.26.0 or later renews its session in the background while it is open; see the app contract.
- **The hostname needs a dot.** An app renews its session in a frame that calls WIP's origin, and the browser sends WIP's login cookie only when the app's origin and WIP's are the same site. A hostname without a dot (`localhost`) or an IP address is its own site, apart from `<label>.<hostname>`, so every renewal would read as a logout. The deployer refuses an install that serves apps (or runs the app edge) on such a hostname. **Locally, the dev target's default hostname is `wip.localhost`:** WIP is then `wip.localhost` and the apps `aa.wip.localhost`, one site, and Chrome and Firefox resolve every `*.localhost` to this machine (not checked here, nor whether Safari does). Changing an existing install's hostname is a re-install (§3.1).
- **DNS:** every `<label>.<hostname>` must reach the install. Names under `localhost` need nothing; for any other hostname, add a wildcard record (`*.<hostname>`) or one per app.
- **TLS:** `tls internal` issues a certificate per app origin from Caddy's CA, which browsers already trust once they trust the install's CA. Attached apps' origins are not known when wip-caddy is rendered: it gets their certificates on demand, and only for a name the app edge confirms as an active attachment. With `tls letsencrypt`, deployer apps get a certificate per origin the same way the hostname does; attached apps' on-demand certificates have not been tried against a public CA yet.
- **k8s:** the Ingress gets a `*.<hostname>` rule and TLS host, and wip-caddy behind it answers each origin on plain HTTP. A self-signed certificate the deployer mints names `*.<hostname>`; an existing self-signed `wip-tls` without it is refused, and the remedy is `kubectl delete secret wip-tls -n <namespace>` and another apply. With `tls external`, your certificate must cover `*.<hostname>`.
- **An install saved with `network.app_origins`** (`path` or `subdomain`, from before this was the only mode) loses the field when it is loaded; one that served apps under `/apps/` moves to app origins on its next apply. Apps' images must already serve at the root (the app contract), and non-browser clients of app URLs (for example a CLI that writes to an app's `server-api`) use the app's origin.

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

### 4.3 API keys: key-file keys vs Console keys

Every API key is a Registry record, and every service resolves keys through
the Registry: key sync carries the keys (every 30 s), the Registry answers
for their grants. Records have two authors:

| Aspect | Key-file keys (wip-deploy) | Console keys |
|---|---|---|
| Authored in | The deployment spec and app manifests, rendered to the key file `config/keys/api-keys.json` | `POST /api/registry/api-keys` (or RC's API-key page) |
| In the Registry | Imported, `managed_by: "wip-deploy"`, `origin: "file"` | `origin: "console"` |
| Changed through the API | No — 409, `error_code: managed_by_deployer`; change them with wip-deploy | Yes |
| Plaintext kept by | The install's secret backend; the app gets it as `WIP_API_KEY` | Nobody: shown once at creation |
| After a MongoDB wipe or restore | Re-imported by the Registry at its next start (and every 10 s) | Gone unless restored from a backup |
| Access on a listed namespace | The grants the file declares; a listed namespace without one reads, by name (the install reserves those names) | Its grant there only: the `namespaces` list is a scope and gives nothing. Creating the key writes a grant on each listed namespace (`grant_permission`, else `read`); the access ends with the grant — revoked, purged by a namespace deletion, or written orphaned by a restore |
| Use case | App keys, standing agent and service keys | Apps wip-deploy does not manage, temporary keys |

**Only the Registry reads the key file.** The deployer renders it into a
directory of its own (`config/keys/`, on k8s the Secret `wip-key-file`),
mounted only into the Registry as `WIP_REGISTRY_KEY_FILE`; no other
container holds a plaintext (`wip-deploy verify --security` checks it). The
Registry imports each key (hashed; the plaintext is never stored), the
grants a key declares and the install's namespace reservations into its own
records, marked `managed_by: "wip-deploy"`. It re-imports at startup, every
10 seconds and whenever a reservation lookup finds a file changed, and
removes records the files no longer declare. A file it cannot trust
(missing, unparseable, or without the `wip-canary` key) changes nothing.

A Console key with a key-file key's name, a grant on a namespace a key-file
key declares, or a runtime reservation of a prefix the file reserves is
replaced by the file's record, and the Registry logs it at CRITICAL. A
Console key with a key-file key's plaintext does not authenticate and is
listed with `shadowed_by`.

If the Registry is down, keys stop working: a service answers 503 once its
synced copy is 90 s old (three missed key syncs), and the Registry's grants are the only
grants. That is the design.

**Spec-declared keys (recommended for standing agent/service keys).**
Declare keys on the deployment and wip-deploy provisions everything:

```bash
wip-deploy install --target dev \
  --api-key '{"name": "web-yac", "namespaces": ["library", "kb"], "grants": {"kb": "write"}}'
```

For each declared key the deployer generates a random plaintext into the
install's secret backend (`~/.wip-deploy/<name>/secrets/<key>-api-key`,
stable across re-applies) and renders it into the key file. `namespaces` is
the key's read scope; `grants` gives per-namespace `read`/`write`/`admin`
and must stay within `namespaces`. A key that declares `grants` has them
owned by the file: the API refuses to change them. A key without `grants`
keeps Registry grants you set through the API. Because the Registry
re-imports the file at every start, a spec-declared key keeps working, at
full declared scope, after any Mongo rebuild (redeploy with a fresh volume,
`nuke --remove-data`, or the wipe-and-restore remediation ritual). Console
keys die in exactly those events unless a system backup restores them (§5.7). The
declaration persists in the install's state: plain `redeploy`/`rebuild`
keep it.

Several keys are more comfortably declared in a file than in repeated
`--api-key` JSON — reviewable and diffable deployer input:

```bash
wip-deploy install --hostname wip.example.com --api-keys-file keys.yaml
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

Other services discover key changes via the Registry's `/api/registry/api-keys/sync` endpoint, polled every 30 seconds. Where the install runs NATS, the Registry also announces every key and grant change (`wip.cache.access.changed`), and each service syncs its keys and drops its cached grants at once — a created, narrowed or revoked key takes effect everywhere within a few seconds. Without NATS, or for a service that missed the message, allow up to 30 seconds (the poll; the 30 s grant cache for a grant change). Under a Registry outage a service keeps its last copy for up to 90 seconds (three missed polls) before refusing runtime keys with 503, so a key revoked during an outage keeps working there for that long.

### 4.5 Namespace scoping and privileged groups

API keys carry a `namespaces` field that scopes their access. The rule is:

- **Privileged groups** — `wip-admins` and `wip-services` — are exempt from namespace scoping. A key in either group has access across all namespaces.
- **Every other key** must declare `namespaces` explicitly. Non-privileged keys without a namespace scope receive 403 on every request.
- **Single-namespace keys** get a convenience: omit the `namespace` query parameter and the server derives it from the key's scope. Multi-namespace keys must always pass `namespace` explicitly.

**Giving an app another namespace on one install.** Each app runs with a key of its own, scoped to the namespaces its manifest declares (`spec.namespaces`, `spec.namespace_access`). Adding a namespace to that scope is an operator decision for one install, not a manifest edit:

```bash
wip-deploy app-namespace add wip-kb kb-research --description "Research corpus"   # owned: reserved for the app, admin on it
wip-deploy app-namespace add dev-kb library --access write                       # used: read or write on a namespace it does not own
wip-deploy app-namespace list wip-kb                                               # manifest + this install + wip
wip-deploy app-namespace remove wip-kb kb-research
```

- The addition is kept in the install's saved spec, so `redeploy` keeps it. Other installs and the app's manifest are untouched.
- Nothing restarts: the app keeps its key, and its scope changes in the key file. The Registry re-imports it within about 10 seconds (about a minute on k8s, while the Secret propagates), and the other services follow within one key sync (30 seconds). The Registry reads the reservation on the next upsert.
- An owned namespace may be created by the app's own key, with the settings given here (`--setting deletion_mode=full`, `--setting isolation_mode=strict`, `--setting allowed_external_refs=a,b`, `--setting id_config=<json>`). The app may send its own description; any other setting must match. Or create it yourself with the master key and let the app use it.
- `remove` takes the namespace away from the app at once, even while it holds the app's data. The namespace and its data stay; deleting them is a separate admin action.
- An app holding the master key (`privileged_key`) takes no additions, and a namespace its manifest already declares cannot be added again.

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

A mapping change restarts nothing. The policy is a file next to the API keys
(`/etc/wip/federated-groups.json`) that the services and the gateway re-read
while running: it applies to the next login within about 10 s on compose and
dev, and within about two minutes on k8s, where the kubelet first refreshes
the mounted file. Tokens already issued keep the groups they were issued
with. `connector add` and `connector remove` restart only Dex.

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
- `user add`, `user set-groups`, `user import` and `user remove` re-render the install's
  files from the current repo but apply only to Dex: Dex is recreated with
  the new user set, and every other container keeps running.

**Users who already have a password.** Users created by hand in a rendered
Dex `config.yaml` are lost on the next re-render. To keep them, and their
passwords, declare them with their existing bcrypt hashes. The hash is
stored as `dex-password-<username>.bcrypt`; no password is generated or
printed, and `user list` shows the user as `supplied hash`.

```bash
# One user, from a file holding the hash ('-' reads stdin)
wip-deploy user add alice --email alice@example.com --group wip-editors \
    --password-hash-file alice.hash --name <install>

# Many users in one apply: from a saved Dex config (its staticPasswords) ...
wip-deploy user import dex-config-backup.yaml --groups-file groups.yaml \
    --group wip-viewers --name <install> --dry-run

# ... or from a CSV file with username,email,hash and an optional groups column
wip-deploy user import users.csv --group wip-viewers --name <install>
```

- A Dex config carries no groups. Each user's groups come from, in order:
  the CSV `groups` column (comma-separated, quoted), its entry in
  `--groups-file` (a YAML mapping such as `alice: [wip-editors, wip-admins]`),
  then `--group`. A user with none refuses the import.
- A user already declared on the install is reported and left as it is.
  Any other problem (a value that is not a bcrypt hash, an email another user
  holds, a repeated username) refuses the whole import before anything
  changes. `--dry-run` lists what would be declared.
- The install declares each user's Dex `userID` as `<username>-001`. An
  imported entry with a different `userID` is warned about: its token
  subject changes.
- The hash has to stay in the secret store. An apply that finds it missing
  stops and names the user; the deployer never invents one.

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
podman restart default_wip-dex
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
podman exec default_wip-mongodb mongodump --out /data/db/backup
cp -r "$WIP_DATA_DIR/mongodb/backup" "./mongodb-backup-$(date +%Y%m%d)"

# PostgreSQL
podman exec default_wip-postgres pg_dump -U wip wip_reporting \
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
headless, scripted, and composable use. Document-store's backup carries
every entity in every status except the inactive template versions nothing
in the archive needs. A version a document is pinned to, or that a carried
template extends, is always kept, and a template whose versions are all
inactive keeps its highest. `include_inactive_templates` keeps every version.

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
grants: a restore brings back data, not access. Access has its own backup
(§5.7).

### 5.7 System backup — API keys, grants, reservations and attachments

Namespace backups never carry auth state. The **system backup** does: every
API key the Registry stores, every namespace grant, every namespace
reservation and every app attachment (§6.5), in one JSON file. It includes
the keys the Registry imported from the install's key file. After a wipe or a
move to other hardware, it brings back the keys apps were issued from the
Console (the apps still hold the plaintexts), the grants to users, groups and
keys, the runtime reservations, and the attached apps' routes.

```bash
# Platform admin key required for both
wip-toolkit --proxy system-backup auth-state.json        # written with mode 0600

wip-toolkit --proxy system-restore auth-state.json       # dry run: what would change
wip-toolkit --proxy system-restore auth-state.json --apply
```

The same is available as `POST /api/registry/system-backup` and
`POST /api/registry/system-restore` (`dry_run=true` by default). There is no
MCP tool.

**What it carries:**
- Each key's bcrypt hash and metadata, never a plaintext.
- No config keys, which includes the master key. They come from the install's
  own config.

**The file is sensitive.** It holds every key hash. Keep it like
`~/.wip-deploy/`. It is not encrypted; encrypt it for offsite storage as
in §5.5.

**How a restore behaves:**
- **Upsert by default.** A key is matched by name, a grant by namespace,
  subject and subject type, a reservation by prefix, and an attachment by
  name. Missing records are created and identical ones left alone. A record
  that differs is listed and skipped unless `--overwrite` (`--mode overwrite`)
  is given. Records that are not in the file are kept.
- **`--mode mirror` makes the file the exact state.** It also replaces what
  differs, and it removes every record the file does not hold:
  - keys go as a revoke takes them, with their grants;
  - grants and reservations are deleted;
  - attachments are deactivated, never deleted.

  It is stack-wide, since every system backup is. It never removes:
  - records the install's files manage, or grants to a key whose grants the
    key file declares;
  - the bootstrap grants;
  - keys named like config keys;
  - user grants, when the file's user grants are not restored (another OIDC
    issuer without `--include-user-grants`);
  - the key the command runs with. A plan that would remove it is listed under
    "Refused" and not applied. The same holds, in `--overwrite` and
    `--mode mirror`, for a plan that would replace that key by the file's
    different record of the same name: a re-created admin key restored from
    an older file would otherwise lock you out.

  Review the dry run, then apply with the plan digest it prints
  (`--mode mirror --apply --expect sha256:…`). The apply is refused if the
  plan has changed since. A removed key stops working at the Registry at
  once, and at the other services on their next key sync (up to about 30 s).
  A mirror is not one transaction: if it fails part-way, re-run it. After a
  mirror the stack holds no keys of its own beyond the file's, so a mirror
  also adopts a file's other hash salt, where an upsert refuses it.
- **A scoped backup covers one app.** `wip-toolkit system-backup aa.json
  --namespace aa --namespace aa-data --attachment aa` carries the grants and
  reservations of those namespaces, the keys whose namespaces all lie in them,
  and the named attachments, and the file declares that scope. Attachments
  have no namespace, so name the ones to carry. A key in a stack-wide group
  (`wip-admins`, `wip-services`) reaches every namespace, so it is never in a
  scope, whatever its namespace list. The backup lists the keys the
  scope touches but cannot carry (a namespace outside it, none, or a
  stack-wide group); the file
  still carries their grants inside the scope, and the restore warns about
  them. **A restore of a scoped file changes nothing outside its scope**:
  - a mirror never removes keys outside the scope, grants and reservations
    on other namespaces, or unnamed attachments;
  - a key whose copy on this stack reaches outside the scope (or holds no
    namespace list, or is in a stack-wide group) is never replaced
    (`out_of_scope_here`);
  - the file's own records outside its declared scope are skipped
    (`outside_archive_scope`).

  An empty `--namespace` or `--attachment` is refused. A scoped file under
  another hash salt is adopted only by a stack with no keys of its own, so in
  practice it is restored onto the stack it came from, or one sharing its
  salt.
- **Restored records are unmanaged** (`origin: restored`). Where the install's
  own key file or reservations file declares the same key, grant or prefix,
  the Registry's import takes it over on its next pass (§4.3).
- **The install's files win.** A restore never replaces a record wip-deploy
  manages on this instance, a grant to a key whose grants the key file
  declares, or a key named like a loaded config key, even with `--overwrite`.
- **The hash salt travels with the file.** Runtime API keys are hashed under
  a salt the Registry keeps for the stack. A new install generates it on the
  Registry's first start; an install that already had keys keeps the old
  default, `wip_auth_salt`. It is fixed for the stack's life. The file
  carries it:
  - onto a stack with the same salt, the restore proceeds as usual;
  - onto a **fresh** stack, one holding no keys of its own (only keys its key
    file manages), the restore adopts the file's salt and re-hashes the managed
    keys. Services verify under it from their next key sync, within 30 s,
    with no restart. This is the disaster-recovery path: a stack rebuilt from
    nothing takes the old stack's keys back;
  - onto a stack with keys of its own, it is refused (409), because adopting
    the salt would invalidate them.

  The salt is not secret from whoever holds the file. Its value is that
  stacks do not share one constant. `WIP_AUTH_API_KEY_HASH_SALT` now only
  salts each process's own config keys.
- **User grants follow the OIDC issuer.** User grants made under a different
  issuer are skipped, since the same email may belong to someone else. Pass
  `--include-user-grants` to restore them anyway. Group and key grants are
  always restored.
- **Attachments come back last,** after keys and grants, with their archived
  status, so an attached app is under the umbrella again as soon as it runs
  with its original key. The attach rules still apply:
  - a name one of this install's own apps routes under is skipped;
  - an upstream that resolves, from the Registry, to one of the install's own
    hosts or to a loopback, link-local or metadata address is skipped. The
    app edge checks again where it resolves (§6.5);
  - an upstream that does not resolve yet is restored and listed as
    `unresolved`, since the app often comes back after the stack;
  - an entry that fails the attach rules is skipped and listed on its own.
    The keys, grants and other attachments are restored all the same;
  - an entry the source's wip-deploy manages is skipped: the install that
    deploys the app re-creates it.
- **Either order.** Every namespace has a `namespace_id`, its identity apart
  from its name, and the archive records the one each grant is for. A grant
  whose namespace is here (a namespace, active or archived, or a reservation,
  holding that id) is written as it was. Any other grant is written
  **orphaned**, with the id: on a name nobody holds here, and on a name held
  here by *another* namespace (a namespace or reservation of the same name
  with another id, such as another owner's reservation). An orphan counts as
  no grant until it is activated, and the report lists it. A key's own
  `namespaces` list does not stand in for it: the list is the key's scope
  and gives no access by itself. A grant the archive holds orphaned never replaces one
  that is effective here. An orphan is activated when a namespace **with its
  id** is created, by whoever creates it, which the ID-preserving namespace
  restore does; or when a namespace admin activates it (`POST
  /api/registry/namespaces/{prefix}/grants/activate`) or grants it again. A
  plain create of the name, or a key that reserves the prefix later and
  creates the namespace, makes another namespace with another id and
  activates none of them. `GET /api/registry/grants?orphaned=true` lists
  every orphan. So the system backup and the namespace backups can be
  restored in either order, and re-running the system restore is safe. A
  disaster recovery done with a *fresh* restore (new ids, a copy) leaves the
  grants orphaned: activate them on the copy if they should apply there.
- **Archives without namespace ids are refused**, system and namespace
  archives alike. To bring one forward, restore it on a WIP from before
  namespace ids, start this version (its startup gives every namespace and
  reservation an id, and deletes orphaned grants it cannot match), and back
  up again.
- **A namespace restore creates namespaces as the requester.** A platform
  admin's namespace restore applies the archive's namespace settings. Anyone
  else's never changes a namespace's settings: an existing namespace keeps
  its own, and a missing one is created with the requester's own
  credential, as reserved (so a reservation's holder gets the reservation's
  settings, and anyone else is refused). Each archive setting not applied is
  a warning on the job. Every namespace create or configure goes out with
  the requester's credential, never document-store's key: an API key, or an
  OIDC login's bearer token, which the Registry validates itself. A login
  forwarded by a gateway has no credential of its own to pass on, so it can
  restore only into an existing namespace, and not as a platform admin
  (whose restore always configures the namespace). One consequence for disaster
  recovery: a reservation holder's restore of a missing namespace gets the
  reservation's `id_config`, not the archive's, so a prefixed id scheme is
  not carried over and new ids are minted the reservation's way from then
  on; the job's warnings say so. Have a platform admin run the restore when
  the archive's settings must apply.
- **The restored namespace's id.** A platform admin's ID-preserving restore
  re-creates the namespace under its archived `namespace_id`, unless a
  namespace on this install already holds that id (restoring beside the
  original makes a copy, with a new id; the job warns). Onto a name reserved
  here for another id it is refused (409): release the reservation first. A
  reservation holder's restore creates the namespace under the reservation's
  id, and a fresh restore under a new one; the job warns when the namespace
  is not the archived one, since the archive's orphaned grants then stay
  orphaned.
- **Restoring onto an instance in use brings back old access.** If the
  instance already holds keys, grants or reservations of its own, the report
  opens with a warning. Access revoked since the backup comes back too:
  review the dry run before `--apply`.

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
  # The admin console is the one app holding the master key; every other app
  # gets a key of its own (spec.namespaces / spec.namespace_access).
  privileged_key: true
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
      - {name: NODE_ENV,      source: {literal: production}}

  routes:
    - {path: /, auth_required: true}

  depends_on: [document-store]

  healthcheck:
    endpoint: /health
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

**1. Build and serve at `/`.** An app is served at the root of its own origin, `https://<label>.<hostname>/`, and is told no base path: Vite's default `base` (`/`) is the right one, and the manifest may not set `APP_BASE_PATH` / `VITE_BASE_PATH` (it is refused). An image still built for `/apps/<slug>/` loads a blank page: its HTML references `/apps/<slug>/assets/...`, which nothing serves.

**2. Internal traffic is HTTP, not HTTPS.** `WIP_BASE_URL` from inside an app container must be `http://wip-caddy:8080`, not `https://wip-caddy:8443`. Caddy's HTTPS listener uses a self-signed cert that Node's fetch / undici rejects even with `NODE_TLS_REJECT_UNAUTHORIZED=0`. The HTTP listener on `:8080` is the right destination for internal hops.

**3. Mount everything at `/`.** Caddy passes the full path to your app: it receives `/health`, `/auth/callback`, and so on. Five paths are the gateway's on every app origin and never reach you: `/auth/handoff`, `/auth/renew`, `/auth/renewed`, `/auth/logout`, `/auth/userinfo`; a manifest route that would cover one is refused.

**4. Sessions behind Caddy.** If your app uses sessions for OIDC:

- `app.set('trust proxy', 1)` — without this Express sees HTTP, not HTTPS, and `Secure` cookies don't go back.
- Cookie path = `/`. Each app has its own origin, so its cookies are its own.

**5. OIDC `state` parameter.** Always include a `state` parameter in the auth request. Dex returns it; `openid-client` / `oauth4webapi` will reject the response without it. Use the library's built-in `authorizationUrl()` rather than constructing the URL manually.

**6. Build args, runtime args, and where each lives.**

| Variable | When | How |
|---|---|---|
| `WIP_BASE_URL` | Runtime | env from manifest (`from_component: router`) |
| `WIP_API_KEY` | Runtime | set by the deployer from the app's own key (`app-<name>`, scoped by `spec.namespaces` / `spec.namespace_access`); only a `privileged_key` app declares `from_secret: api-key` |
| `OIDC_ISSUER` | Runtime | env (the *external* URL — `https://<hostname>:8443/dex`) |

### 6.4 Common app failures

| Symptom | Probable cause | Fix |
|---|---|---|
| 502 on API calls from the app | TLS error proxying to `https://wip-caddy:8443` | Use `http://wip-caddy:8080` |
| 401 on API calls | `WIP_API_KEY` mismatch / stale | Recreate containers so all read the current secret |
| Blank page; 404 on `/apps/<slug>/assets/...` | Image built for the old `/apps/<slug>/` base | Rebuild with Vite's default base `/` (§6.3 gotcha 1) |
| OIDC "unexpected state" | `state` parameter missing | Use the OIDC library's `authorizationUrl()` |
| OIDC redirect to wrong app | Callback not on the app's origin | The deployer registers `https://<label>.<hostname>/<redirect_path>`; mount the callback at that path |
| Session lost after Dex redirect | `trust proxy` not set, or wrong cookie path | `app.set('trust proxy', 1)`; cookie path = `/` |
| `${VAR}` literal in container | Env var not resolved | Verify the manifest's `env.required` lists the var with a real `source:` |

### 6.5 Attached apps — under the umbrella without wip-deploy

An app whose own admin deploys it, anywhere the stack's router can reach, can
be **attached** under the umbrella through the Registry's API instead of being
deployed by wip-deploy. Its key is an ordinary Console key (§4.3). The
deployer knows nothing about the app.

**Served by the app edge.** The `app-edge` component reads the active
attachments every 10 s with its own key, and serves each one at the root of
its own origin, `https://<name>.<hostname>[:port]/`: behind the gateway with
the record's `allowed_groups`, then to the upstream, every path as the
browser sent it. The public router hands it every origin under the hostname
that no deployed app has.

**The edge is an optional module, off by default.** No preset includes
it: turn it on with `--add app-edge` at install, or `wip-deploy add-module
app-edge` on an existing install, and off again with `wip-deploy
remove-module app-edge`. It needs the auth gateway and core; the deployer
refuses it otherwise. Installs made before the default changed keep the edge
they run. Without the edge:

- an origin under the hostname that no deployed app has answers 404 (on
  k8s) or gets no certificate (compose and dev);
- the Registry refuses every attachment write (`invalid_state`,
  `details.rule: no_app_edge`), reviving included. Listing, reading and
  deactivating still work, so records from before the edge was removed stay
  manageable;
- records already active stay active but are not served, so their apps go
  offline until the edge is back. Reads say so: every attachment in a list or
  get carries `served` (true when it is active and an edge runs) and, when
  false, `not_served_reason`: `inactive`, `upstream_invalid` (a record
  stored before the host rule below: the edge refuses its host),
  `no_app_edge`, or `policy_unavailable` (the Registry cannot use the attach
  policy file; the app edge reads the file on its own, so what it serves may
  differ). `served` is not a reachability check: the Registry never
  calls an upstream, and the edge can still refuse a record when it resolves
  it;
- a system restore skips the archive's active attachments
  (`reason_code: no_app_edge`) and restores its inactive ones as records.

What the edge guarantees:

- **It dials the address it checked.** Each upstream host is resolved and
  every address checked against the blocklist below, and the edge connects
  to exactly those addresses. The name is used only for TLS and, with
  `upstream_host_header`, the Host header. A name that later resolves
  somewhere else doesn't reach a connection.
- **Identity:** the app receives `X-WIP-User` / `X-WIP-Groups` from the
  gateway, never a key. Browser-sent identity headers and the gateway's
  `wip_session` cookie are removed. The app's own cookies pass through.
  `X-Forwarded-Proto` / `X-Forwarded-Host` name the app's own origin as the
  browser addressed it.
- **No last-known-good copy:** the edge polls every 10 s. The first poll
  that finds the Registry silent for more than 30 s (in practice between 30
  and about 45 s after its last answer, since a failing poll takes longer)
  turns every attached app to 503 until the Registry answers again.
- **A dead upstream address is skipped:** a dial times out after 2 s, the
  next checked address is tried for up to 10 s, and a failed address is
  avoided by later requests for 30 s.
- **One bad record can't take the others down.** A record the edge can't
  serve is skipped and logged. If Caddy still refuses a config, the edge
  retries without the records that carry their own CA certificate.

```bash
curl -k -X POST https://<host>/api/registry/attachments \
  -H "X-API-Key: <admin-key>" -H "Content-Type: application/json" \
  -d '[{"name": "aa", "upstream": "https://aa.apps.example:3000",
        "auth_required": true, "allowed_groups": ["aa-users"],
        "display_name": "AuthorAssist"}]'
curl -k https://<host>/api/registry/attachments -H "X-API-Key: <admin-key>"
curl -k -X POST https://<host>/api/registry/attachments/deactivate \
  -H "X-API-Key: <admin-key>" -H "Content-Type: application/json" -d '[{"name": "aa"}]'
```

- **One record per app**, keyed by `name`, owning the origin
  `<name>.<hostname>`. The name is that origin's first DNS label: lowercase
  letters, digits and inner hyphens, 2 to 41 characters, starting with a
  letter and ending with a letter or digit. The route fields match a manifest's route:
  `auth_required`, `allowed_groups`, `upstream_host_header`, `streaming`.
  An attachment is served at its origin's root, so there is no prefix
  setting; a record stored with `strip_prefix`,
  `preserve_prefix_subpaths` or `redirect_bare_path` loses them once at
  the Registry's startup, and a system archive carrying them is refused
  (restore it on an install from before, and back that install up again). Then `upstream_tls` (`verify`, and
  an optional `ca_pem` holding the CA certificate itself), `display_name`,
  `ui_only` and `health`. Writes are bulk upserts: a request whose body
  validates answers 200 with per-item results; an item the request schema
  refuses rejects the whole request with 422, and nothing is written. **Never deleted:** deactivate it, and write it
  again to revive it.
- **A write replaces the whole record.** `auth_required` and
  `allowed_groups` are required on every write (`[]` means any logged-in
  user). A client that sends only some fields fails with 422 rather than
  silently opening the app to more users. Send the full record you read.
- **The upstream is scheme, host and optional port only:** no path,
  credentials, query or fragment, and no port 0 (omit the port for the
  scheme's default). The router proxies every path of the
  app's origin to the same path on the upstream.
- **The host is an IP literal or a DNS name of RFC 1123 labels:** letters,
  digits and inner hyphens, 1 to 63 characters each. An underscore (common
  in container names), an empty label or a non-ASCII name is refused with
  `validation_failed`, `details.rule: upstream_invalid`; give an
  internationalised name in its `xn--` form. The app edge applies the same
  rule, so a record the Registry accepts is one the edge can serve.
- **Reading** (`GET /attachments`) is open to platform admins and to the
  router's app edge, whose key's group `wip-app-edge` may read attachments
  and nothing else. Every write stays platform admin. A record the deployer
  manages is refused with `managed_by_deployer`.
- **Platform admin only. Attaching is trust.** An attached app is served on
  an origin of its own with a session of its own, so it never receives a
  visitor's WIP session; but the gateway tells it who its visitors are.
  Attach only apps you trust with your users' identities.
- **Refused names:** the label of an app this install deploys
  (`name_collision`): its origin is taken. An apply that would add an app
  with an attachment's name as its label, active or not, is refused in
  turn.
- **Refused upstreams** (`forbidden`, `details.rule: upstream_blocked`):
  anything resolving to one of the install's own hosts (the deployer
  renders them into `WIP_ATTACH_POLICY_FILE`: its containers and services,
  its public hostname, on k8s the API server's names, and on compose and
  dev the host's container aliases `host.containers.internal` and
  `host.docker.internal`), or to a
  loopback, link-local (cloud metadata), unspecified or multicast address,
  or to the cloud metadata addresses outside link-local (`fd00:ec2::254`,
  `100.100.100.200`). IPv4-mapped IPv6 addresses count as the IPv4 address
  they carry. A missing, empty or unparseable policy file refuses every
  attach. Addresses
  are compared, not names. This attach-time check is advisory: the Registry
  may sit on another network than the router (a backend on one cluster, the
  umbrella on another). The authoritative check is the router edge's, when
  it resolves the upstream. A host that doesn't resolve from the Registry
  is accepted with a warning.
- **On compose and dev, the host is refused too.** The host publishes the
  front door's port, so an upstream on it loops back through the front
  door. The edge refuses the host's addresses as well as its aliases: its
  default gateway on the install's network, read at start. An app running
  directly on the compose host cannot be attached; run it in a container on
  the install's network (`<install>_wip-network`) instead. An install host that stops
  resolving for a moment (a container being recreated) keeps blocking the
  addresses it last resolved to.
- **On k8s,** a blocked name resolves to its Service's ClusterIP, so an
  upstream given as a WIP *pod* IP isn't caught by the name check. The
  rendered NetworkPolicy `app-edge-egress` takes the edge out of the
  namespace's broad allows. It lets the edge reach only the Registry, the
  auth gateway, DNS, pods in other namespaces and addresses outside the
  cluster.
  - **Give the cluster's own ranges at install:** `--cluster-cidr
    pods=<CIDR> --cluster-cidr services=<CIDR> --cluster-cidr nodes=<CIDR>`
    (repeatable; `platform.k8s.cluster_ranges`). They are excepted from the
    "outside the cluster" rule (`ipBlock 0.0.0.0/0`, `::/0`), which
    otherwise also reaches the API server's endpoint and the kubelets on the
    node addresses (ports such as 6443/16443 and 10250) and, depending on
    the network plugin, pod and Service IPs. The node ranges also join the
    attach blocklist: the Registry and the edge refuse an upstream that
    resolves into them. `wip-deploy` warns about each kind left out.
    On an existing install, `wip-deploy redeploy --name <install>
    --cluster-cidr …` replaces the saved ranges (pass every kind to keep).
    Give IPv4 ranges as IPv4: an IPv4-mapped IPv6 range is refused, since
    it would never match.
  - **Node ranges are the cluster's own node addresses** (for example
    `192.168.1.20/32`), never a LAN it shares with other machines: an app
    another cluster on that LAN serves through a NodePort would be refused
    too. **Where the API endpoint is not a node address,** name it as
    well: a load balancer's virtual IP in front of an HA control plane, or
    a managed cluster's API endpoint. On kubeadm the API server runs as a
    hostNetwork pod in `kube-system`; whether the "other namespaces" rule
    matches hostNetwork pods depends on the network plugin, so give the node
    addresses there too. Pod and Service ranges never block an upstream, since attached
    apps in other namespaces live there.
  - **The edge still reaches every other Kubernetes namespace's pods,** on
    any port, including kube-system and other tenants' services. That is
    part of "attaching is full trust" (above): an attachment can publish an
    in-cluster service that has no authentication of its own. Attaching is
    platform admin only.
- **The app's key** is created in RC or through `/api-keys` (§4.4),
  widened with `reserve` for the namespaces it creates. The app holds the
  plaintext itself.

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
| **Runtime keys** (console / `POST /api/registry/api-keys`) | Registry (MongoDB, bcrypt hash) | revoke + create a new one; other services see the change within a few seconds (NATS), at most the 30 s key sync |

`rotate-key` names the secret file that holds the new value
(`~/.wip-deploy/<name>/secrets/<secret>`), not the value itself, so the key
does not land in a terminal log or an agent's transcript; `--show-key` also
prints the plaintext once. It then names the services the apply
restarted, and probes the install's public URL: the Registry must
accept the new key and refuse the old one, or the command fails (`--no-check`
skips the probe). There is no grace window — once the apply completes, the
old value gets 401.

After rotating:

- Clients that read the key **file** need no config change, but only some
  re-read it while running: `kbc` reads it on every call and an MCP server
  configured with `WIP_API_KEY_FILE` re-reads it when it changes, so both
  follow at once; app servers using `apiKeyFile` follow on their next
  restart.
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

**The secret store travels with the install, whole.** An install's
`secrets/` folder holds the passwords its datastores were set up with. Every
apply records a short fingerprint of each secret it ran with in
`deployment.deployer-state` (never the values) and, before generating
anything, checks the folder against that record, and on k8s against the live
`wip-secrets` Secret too. If a known secret is missing, or holds a different
value, the apply stops and names the secrets, instead of generating
passwords the running datastores do not hold. To recover:

- copy the install's complete `~/.wip-deploy/<name>/` from the machine that
  last drove it, `secrets/` included;
- on k8s, `wip-deploy secrets pull --name <install>` writes the running
  values from the cluster into the folder;
- if the folder is right and only the record is stale (say, the folder was
  restored from a backup), `wip-deploy secrets accept --name <install>`
  records it.

A secret the install has never had, such as one a platform upgrade or a new
app introduces, is still generated, and the apply prints its name.

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

Every WIP service exposes `/health` (and document-store, template-store and reporting-sync a `/health/integrity` check), but in a non-techie deployment nobody is watching them. `wip-toolkit status` aggregates these and exits non-zero on anomalies — wire it to cron + email and the install will tell you when something is wrong:

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
| App's static assets return 404 with MIME-type errors | §6.3 gotcha 1 | Rebuild the image with Vite's default base `/` |
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
