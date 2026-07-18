# Bootstrap Bundle — Phase 1 (Hand-Crafted YAML)

**Status:** Phase 1 shipped (CASE-373 Phase 1). Phase 2 (Registry export endpoint) and Phase 3 (Console UI) tracked in CASE-373.

## What it is

A *bootstrap bundle* is a single YAML file that seeds an apps-only `wip-deploy` install with everything it needs to talk to a remote WIP:

- The cloud's external base URL the apps will connect to
- A scoped, time-limited API key for those apps to authenticate
- The cloud's internal CA cert (PEM) so the apps can trust the cloud's TLS

In v1 the bundle is hand-crafted by an operator on the cloud side and consumed via `wip-deploy import-bundle` on the laptop side. Future phases ship Console-side bundle generation (`POST /api/registry/bundles/export`) and a download UI; the consumer side stays unchanged across phases.

The full design lives in **CASE-373**. Open follow-up cases:
- **CASE-380** — broader CA-rotation UX beyond `--update-ca-only`
- **CASE-381** — bundle-revocation listing for Console v2

## When to use it

You want to run apps (react-console, clintrial, …) on a laptop pointed at a WIP install running somewhere else — typically the Pi, a cloud VM, or any host the laptop can reach over HTTPS. The bundle replaces the eight-step manual flow documented in CASE-373's Problem section.

## v1 cookbook — hand-crafting a bundle on the cloud side

Run these on the cloud host where WIP lives. The output is a single file you scp to the laptop.

### 1. Generate a scoped API key

The bundle's API key must be scoped to the namespaces the laptop apps need and **default to least-privilege** — read-only unless write is explicitly required (CASE-373 caveat #1).

```bash
# On the cloud host:
curl -X POST https://wip.example.com/api/registry/api-keys \
  -H "X-API-Key: $MASTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "laptop-rc",
    "owner": "you@example.com",
    "description": "Bootstrap bundle for laptop running react-console",
    "namespaces": ["kb"],
    "expires_at": "2027-05-14T00:00:00Z"
  }' | jq -r .plaintext_key > /tmp/laptop-rc.key
```

Save the printed `plaintext_key` (it's only returned once). The key you'll embed in the bundle is the value of this file.

### 2. Export the cloud's internal CA

```bash
# On the cloud host:
wip-deploy export-ca --out /tmp/cloud-ca.pem
```

This works for `--tls internal` installs (the compose/dev default). Let's Encrypt and external-TLS installs skip the CA bundle — the OS already trusts those certs.

### 3. Assemble the bundle YAML

Paste the API key value and the PEM contents into the template below. Replace `<...>` placeholders. The schema is enforced by `wip-deploy import-bundle` — see the rejection paths in `deployer/src/wip_deploy/import_bundle.py`.

```yaml
api_version: wip.dev/v1
kind: BootstrapBundle
metadata:
  name: laptop-rc                              # human-readable; matches the api-key name
  generated_at: 2026-05-14T18:00:00Z           # UTC ISO 8601
spec:
  external_base_url: https://wip.example.com   # no trailing path, no query
  api_key:
    value: <plaintext key from step 1>
    name: laptop-rc
    scope:
      namespaces: [kb]                         # one or more, matching step 1
      permissions: read                        # REQUIRED — no implicit-write default
    expires_at: 2027-05-14T00:00:00Z           # must match step 1
  ca_cert: |                                   # paste the PEM verbatim, including BEGIN/END
    -----BEGIN CERTIFICATE-----
    MIID...
    -----END CERTIFICATE-----
  suggested_apps:
    - name: react-console
```

### 4. Ship it to the laptop

scp, USB stick, encrypted email — any channel that gets the file to the laptop intact. The bundle is a long-lived credential; treat it like a password.

### 5. Import on the laptop

```bash
# On the laptop:
wip-deploy import-bundle laptop-rc.yaml --name laptop-rc
```

Output:

```
✓ Imported bundle into /Users/you/.wip-deploy/laptop-rc
  External base URL: https://wip.example.com
  API key 'laptop-rc' (scope: kb, permissions: read, expires: 2027-05-14)
  CA fingerprint: a1:b2:c3:d4:...

Suggested install command:
  wip-deploy install --name laptop-rc --target dev --apps-only \
    --remote-wip https://wip.example.com --app react-console
```

Run the suggested command. Install auto-detects `secrets/external-ca.crt` and mounts it into every app container at `/etc/ssl/certs/external-ca.crt` plus injects `NODE_EXTRA_CA_CERTS` pointing at it. No `NODE_TLS_REJECT_UNAUTHORIZED=0` needed.

## CA rotation

The cloud's internal CA can be rotated (security event, planned cycle, container rebuild). When it happens, every previously-imported laptop has a stale CA and silently breaks TLS. The Phase 1 primitive for the laptop side:

```bash
# On the laptop, after fetching a fresh bundle:
wip-deploy import-bundle laptop-rc.yaml --name laptop-rc --update-ca-only
```

This refreshes `secrets/external-ca.crt` only — the api-key and other state are untouched. CASE-380 tracks the broader detection-and-notification UX.

## Schema reference

| Field | Required | Notes |
|-------|----------|-------|
| `api_version` | yes | Must be `wip.dev/v1` |
| `kind` | yes | Must be `BootstrapBundle` |
| `metadata.name` | yes | Human-readable bundle name |
| `metadata.generated_at` | yes | ISO 8601 timestamp (UTC) |
| `spec.external_base_url` | yes | https:// URL, no trailing path/query |
| `spec.api_key.value` | yes | Plaintext key from `POST /api-keys` |
| `spec.api_key.name` | yes | Matches the Registry api-key's name |
| `spec.api_key.scope.namespaces` | yes | Non-empty list of namespace prefixes |
| `spec.api_key.scope.permissions` | **yes** | `read` or `write`. No default — explicit is the rule. CASE-373 caveat #1. |
| `spec.api_key.expires_at` | yes | ISO 8601; rejected if already past at import time |
| `spec.ca_cert` | yes | PEM block (`-----BEGIN CERTIFICATE-----` … `-----END CERTIFICATE-----`) |
| `spec.suggested_apps` | no | List of `{name: ...}` entries |

## Phase 2 preview (CASE-373)

When Phase 2 ships, steps 1–3 collapse into a single `wip-deploy export-bundle` call on the cloud side:

```bash
# On the cloud host (Phase 2):
wip-deploy export-bundle --name laptop-rc --namespace kb \
  --apps react-console --ttl 1y --out laptop-rc.yaml
```

The Registry mints the key, exports the CA, signs the bundle, and writes the file. Step 4 (transport) and step 5 (import) stay the same — Phase 1 forward-compatibility is preserved by reserving the `signing_pubkey` and `signature` fields in v1.

## See also

- CASE-373 — design parent (Phases 1, 2, 3)
- CASE-380 — CA-rotation UX follow-up
- CASE-381 — bundle-revocation listing
- CASE-358 — cross-host `--remote-wip` plumbing
- CASE-359 — `--apps-only` mode
- CASE-360 — `wip-deploy export-ca` verb (used in step 2)
