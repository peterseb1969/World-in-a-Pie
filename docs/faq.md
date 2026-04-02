# WIP Frequently Asked Questions

> **See also:** [Project FAQ](../faq.md) for high-level questions about WIP's philosophy, architecture, and use cases.

Common issues and solutions for World In a Pie deployments.

---

## Networking & Hostnames

### Why should I use `.local` in my hostname?

**Always use the full `.local` suffix for mDNS hostnames:**

```bash
# Correct
./scripts/setup.sh --hostname pi-poe-8gb.local --preset standard

# Problematic
./scripts/setup.sh --hostname pi-poe-8gb --preset standard
```

**Why it matters:**

| Context | `pi-poe-8gb` | `pi-poe-8gb.local` |
|---------|--------------|---------------------|
| SSH | Works | Works |
| Browser URL bar | Triggers web search | Recognized as hostname |
| Dex redirects | May fail | Works correctly |

**The problem:** Web browsers interpret short hostnames without a recognized TLD as search queries. You'd need to manually type `https://pi-poe-8gb:8443` every time.

**The solution:** The `.local` TLD is reserved for mDNS/Bonjour. Browsers recognize it as a valid domain and treat it as a URL, not a search.

### I used the wrong hostname during setup. How do I fix it?

The cleanest solution is to re-run setup.sh with the correct hostname:

```bash
./scripts/setup.sh --hostname pi-poe-8gb.local --preset standard -y
```

This regenerates all configuration files:
- `config/caddy/Caddyfile` - TLS certificates and reverse proxy
- `config/dex/config.yaml` - OIDC issuer and redirect URIs
- `.env` - Environment variables for all services

Then restart the stack for changes to take effect.

**Manual fix (not recommended):** If you must patch manually, update these files and restart the affected containers:
1. `config/caddy/Caddyfile` - Add hostname to server block
2. `config/dex/config.yaml` - Update issuer and redirectURIs
3. `.env` - Update `WIP_HOSTNAME`, `VITE_OIDC_*` variables
4. Restart: `podman restart wip-caddy wip-dex` and recreate wip-console

---

## Authentication

### "Authentication required" error after login

If you can log in via Dex but then see "Authentication required" errors when accessing services:

1. **Check JWT issuer URL matches:** The services validate JWTs against the issuer URL. If you access via `pi-poe-8gb.local` but the token was issued by `pi-poe-8gb`, validation fails.

2. **Solution:** Ensure consistent hostname everywhere - re-run setup.sh with the correct `--hostname`.

### "Session expired" immediately after OIDC login

**Cause:** The JWT issuer URL in the token doesn't match what the backend services expect. This happens when Dex's `issuer` config, `.env`'s `WIP_AUTH_JWT_ISSUER_URL`, and `VITE_OIDC_AUTHORITY` don't all agree.

**These three must be identical:**

| Config | Variable | Example |
|--------|----------|---------|
| `config/dex/config.yaml` | `issuer` | `https://localhost:8443/dex` |
| `.env` | `WIP_AUTH_JWT_ISSUER_URL` | `https://localhost:8443/dex` |
| `.env` | `VITE_OIDC_AUTHORITY` | `https://localhost:8443/dex` |

**Solution:** Re-run `setup.sh` with the correct `--hostname`. It generates all three from a single source. Then **recreate** (not just restart) containers:

```bash
# CORRECT - picks up new .env
podman-compose down && podman-compose up -d

# WRONG - env vars not reloaded
podman-compose restart
```

**Verify:**
```bash
grep issuer config/dex/config.yaml
podman exec wip-def-store printenv | grep WIP_AUTH_JWT_ISSUER_URL
```

Both should show the same URL.

### Browser shows "ERR_SSL_PROTOCOL_ERROR"

Caddy doesn't have a TLS certificate for the hostname you're using.

**Check Caddy logs:**
```bash
podman logs wip-caddy | grep -i "certificate\|tls"
```

**Solution:** The hostname in your browser must match one configured in the Caddyfile. Re-run setup.sh with the correct `--hostname` or manually add the hostname to the Caddyfile and restart Caddy.

---

## Containers

### How do I check if all services are running?

```bash
podman ps --format "table {{.Names}}\t{{.Status}}"
```

All core services should show "Up" with "(healthy)" status.

### A service shows "unhealthy"

Check the service logs:
```bash
podman logs <container-name>
```

Common causes:
- **Database not ready:** Service started before MongoDB/PostgreSQL was healthy
- **Network issues:** Service can't reach dependencies
- **Configuration error:** Check environment variables in `.env`

**Quick fix:** Restart the unhealthy service:
```bash
podman restart <container-name>
```

### All containers die after SSH disconnect / logout

**Symptom:** Containers run fine while you're connected, but stop when you disconnect SSH or log out.

**Cause:** Rootless Podman runs containers in your user session. Without "linger" enabled, systemd kills all user processes when you log out.

**Check:**
```bash
loginctl show-user $USER | grep Linger
# Linger=no  <- Problem!
```

**Fix:**
```bash
sudo loginctl enable-linger $USER
```

After this, containers will persist across logouts, SSH disconnects, and client machine sleep cycles.

**Note:** The setup script now enables linger automatically on Linux.

**Why Docker doesn't have this problem:** Standard Docker runs as a system daemon (`dockerd`) under root. Podman in rootless mode runs containers in your user session. Rootful Podman (`sudo podman`) also avoids this issue.

| | Rootless Podman | Rootful Podman | Docker |
|---|---|---|---|
| Linger required | Yes | No | No |
| UID mapping issues | Yes | No | No |
| Runs as root | No | Yes | Yes |
| Security isolation | Best | Standard | Standard |

For a dedicated WIP server (Pi), rootful Podman or Docker may be simpler. Rootless is better for multi-user or exposed environments.

---

## Development

### How do I seed test data on a remote Pi?

```bash
# Create a venv (one-time)
python3 -m venv ~/wip-venv
source ~/wip-venv/bin/activate
pip install faker requests

# Run seed script
cd ~/World-in-A-pie  # or your WIP directory
python scripts/seed_comprehensive.py --host localhost --profile standard
```

### How do I view MongoDB data directly?

If dev-tools module is enabled, Mongo Express is available:
- URL: `http://<hostname>:8081`
- Credentials: admin / admin

Or via CLI:
```bash
podman exec -it wip-mongodb mongosh
```

---

## See Also

- [Authentication Guide](authentication.md) - Detailed auth configuration
- [Network Configuration](network-configuration.md) - All 4 deployment scenarios
- [Container Inventory](container-inventory.md) - Detailed service specs and Pi deployment
