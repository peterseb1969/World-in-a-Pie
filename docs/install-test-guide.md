# WIP Install Test — Step by Step

## For the release builder (Peter on Mac)

### 1. Build and push images

```bash
# Login to Gitea (first time only)
podman login --tls-verify=false gitea.local:3000 -u peter

# Build all images and push
scripts/build-release.sh \
  --registry gitea.local:3000/peter \
  --tag <version> \
  --push \
  --insecure
```

### 2. Prepare the install kit

The install kit is a directory with these files:

```
wip-install/
├── docker-compose.production.yml    ← from repo root
├── .env.production.example          ← from repo root
├── config/production/
│   ├── Caddyfile.template           ← from config/production/
│   └── dex-config.template          ← from config/production/
└── scripts/
    └── setup-wip.sh                 ← from scripts/
```

Update the image tag in `docker-compose.production.yml` if needed:
```bash
sed -i 's/:old-tag/:new-tag/g' docker-compose.production.yml
```

Copy the kit to the target machine:
```bash
scp -r wip-install/* peter@<target>:~/wip/
```

---

## For the installer (on target machine)

### Prerequisites

- Linux with `podman` + `podman-compose` (or Docker + docker-compose)
- Network access to `gitea.local:3000` (or wherever images are hosted)

### One-time: configure insecure registry (HTTP only)

```bash
mkdir -p ~/.config/containers
cat > ~/.config/containers/registries.conf << 'EOF'
[[registry]]
location = "gitea.local:3000"
insecure = true
EOF
```

### Step 1: Setup

```bash
cd ~/wip
bash scripts/setup-wip.sh <your-hostname>
```

This generates:
- `.env` with your hostname, auto-generated passwords, and OIDC config
- `config/caddy/Caddyfile` from template
- `config/dex/config.yaml` from template

### Step 2: Start

```bash
podman-compose -f docker-compose.production.yml up -d
```

Wait ~45 seconds for health checks.

### Step 3: Verify

```bash
podman ps --format '{{.Names}} {{.Status}}' | sort
```

All services should show `healthy`.

### Step 4: Login

Open in browser (incognito recommended for first login):

```
https://<your-hostname>:8443
```

Accept the self-signed certificate. Login:

| User | Password | Access |
|------|----------|--------|
| admin@wip.local | admin | Admin |
| editor@wip.local | editor | Write |
| viewer@wip.local | viewer | Read |

You should see the `wip` namespace with 2 system terminologies.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `https: server gave HTTP response to HTTPS client` | Configure insecure registry (see prerequisites) |
| MongoDB shows `unhealthy` | Wait 30s — initial setup is slower on ARM |
| `No Namespace Access` after login | Use incognito window. Verify Dex image is v2.45+ |
| Services fail to start (dependency errors) | `podman pod rm -f -a` then re-run `podman-compose up -d` |
