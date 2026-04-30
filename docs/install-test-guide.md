# WIP Install Test — Step by Step

The reproduction procedure for the v1.0 install-test acceptance criterion: a non-techie can install WIP + a frozen app and explore real data within an hour, from a single page.

Two halves: the release builder (Peter on Mac, cutting a release) and the installer (the install-test reproducer, on a fresh target machine).

---

## For the release builder (Peter on Mac)

### 1. Build and push images

```bash
# Login to the registry (first time only)
podman login --tls-verify=false gitea.local:3000 -u peter

# Build all service images and push them
scripts/build-release.sh \
  --registry gitea.local:3000/peter \
  --tag <version> \
  --push \
  --insecure
```

Multi-arch builds (e.g., for a Pi target from a Mac):

```bash
scripts/build-release.sh \
  --registry gitea.local:3000/peter \
  --tag <version> \
  --platforms linux/amd64,linux/arm64 \
  --push \
  --insecure
```

`scripts/build-release.sh --help` lists the full flag set.

### 2. Tag the release

```bash
git tag v<version>
git push origin v<version>
git push gitea v<version>
```

That's it — the deployer pulls images from the registry at install time, so there's no install-kit to package and ship. The install-test reproducer just clones the repo and runs `wip-deploy`.

---

## For the installer (the install-test reproducer)

### Prerequisites

- Linux, macOS, or Windows (WSL2) with `podman` + `podman-compose` (or Docker + docker-compose)
- ~10 GB free disk
- Network access to wherever the images are hosted (e.g., `gitea.local:3000`)
- Python 3.11+ (the deployer is a Python tool)

**Hardware notes for Pi targets:**

- Use a Raspberry Pi 5, not a Pi 4. The architecture (MongoDB + PostgreSQL + NATS + MinIO + 6 services + Caddy) is a step beyond what a Pi 4 handles gracefully.
- Use an SSD, not an SD card. MongoDB writes, NATS JetStream persistence, PostgreSQL, and MinIO compound on slow storage.

### 0. One-time: configure insecure registry access (HTTP only)

Skip this step if your registry uses real TLS.

```bash
mkdir -p ~/.config/containers
cat > ~/.config/containers/registries.conf << 'EOF'
[[registry]]
location = "gitea.local:3000"
insecure = true
EOF
```

### 1. Clone the repo

```bash
git clone https://github.com/peterseb1969/World-in-a-Pie.git
cd World-in-a-Pie
```

### 2. Install

```bash
wip-deploy install \
  --preset standard \
  --target compose \
  --hostname <your-hostname> \
  --tls internal
```

This generates the compose manifest, creates `.env` with auto-generated random secrets stored in the secrets backend (default: `~/.wip-deploy/<install-name>/secrets/`), and brings the stack up.

For an internet-exposed install with Let's Encrypt, replace `--tls internal` with `--tls letsencrypt` and use a hostname that resolves on the public internet with port 443 reachable.

### 3. Verify

Wait ~45 seconds for health checks to settle, then:

```bash
wip-toolkit status                      # Aggregated health + reporting metrics
podman ps --format '{{.Names}} {{.Status}}' | sort   # Per-container view
```

All services should show `healthy` and `wip-toolkit status` should exit 0.

### 4. Retrieve admin credentials

Random secrets live in the secrets backend, not in a checked-in file. For the file backend (the default for `--target compose`):

```bash
ls ~/.wip-deploy/default/secrets/        # one file per secret name, mode 0600
cat ~/.wip-deploy/default/secrets/dex-password-admin
cat ~/.wip-deploy/default/secrets/api-key
```

For Kubernetes installs (`--target k8s`), the secrets live as a `wip-secrets` Secret in the deployment's namespace:

```bash
kubectl get secret wip-secrets -n <ns> -o jsonpath='{.data.dex-password-admin}' | base64 -d
```

Save the credentials in a password manager before doing anything else. Losing them means re-installing.

### 5. Login

Open in a browser (incognito recommended for first login):

```
https://<your-hostname>:8443
```

Accept the self-signed certificate (only for `--tls internal` — Let's Encrypt installs are pre-trusted). Login with the admin user (`admin@wip.local` by default) and the password from step 4.

You should see the `wip` namespace with the system terminologies populated.

---

## Acceptance Criterion

The v1.0 install-test passes if a non-techie installer reaches the login screen and explores at least one real document or term within one hour of starting from this page.

If they fail at any step, the failing step is the source of the install-test feedback — not "the installer didn't try hard enough." The doc has to make every step non-ambiguous on the first read.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `https: server gave HTTP response to HTTPS client` from the registry | Configure insecure registry access (step 0) |
| MongoDB shows `unhealthy` | Wait 30s — initial setup is slower on ARM. SSD storage drops this dramatically |
| `No Namespace Access` after login | Use an incognito window. Verify the Dex image version pinned in the deployer is current |
| Services fail to start with dependency errors | `wip-deploy nuke --remove-data --remove-secrets`, then re-run `wip-deploy install` |
| Login screen reachable but credentials rejected | Check that you copied the password from the right secrets file (each install has its own backend directory) |
| 401 errors after a successful login | Three-Value OIDC Rule mismatch. See [WIP Guide §3.1](wip-guide.md). All three of `config/dex/config.yaml` `issuer`, `.env` `WIP_AUTH_JWT_ISSUER_URL`, and `.env` `VITE_OIDC_AUTHORITY` must match exactly. After fixing, recreate containers (`podman-compose down && podman-compose up -d`); restart does not pick up env changes |

---

## Related

- [WIP Guide](wip-guide.md) — the canonical operator-facing reference for deploy + auth + networking + apps + security.
- [Production Deployment](wip-guide.md#2-deployment-tiers) — Tier 1 / Tier 2 / Tier 3 framing.
- [Release Checklist](release-checklist.md) — the pre-tag verification this install-test gates.
