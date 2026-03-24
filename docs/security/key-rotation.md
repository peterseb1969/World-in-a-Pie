# Key Rotation Procedures

This guide covers rotating secrets and keys in a WIP deployment.

---

## API Key Rotation

### Rotating the Master API Key

1. **Generate a new key:**
   ```bash
   ./scripts/security/generate-api-key.sh --name master-key-v2
   ```

2. **Update .env file:**
   ```bash
   # Edit .env and replace the API_KEY values
   API_KEY=<new-key>
   WIP_AUTH_LEGACY_API_KEY=<new-key>
   MASTER_API_KEY=<new-key>
   ```

3. **Recreate services** (restart does NOT reload env vars):
   ```bash
   podman-compose down && podman-compose up -d
   ```

4. **Update clients** using the old key

5. **Verify** the new key works (via Caddy proxy):
   ```bash
   curl -k -H "X-API-Key: <new-key>" https://localhost:8443/api/registry/namespaces
   ```

### Rotating Service-Specific Keys

For deployments using `api-keys.json`:

1. Generate new key with same groups
2. Add new key to `api-keys.json`
3. Deploy updated config
4. Update clients to use new key
5. Remove old key from `api-keys.json`
6. Redeploy

---

## Database Password Rotation

### MongoDB

1. **Connect to MongoDB:**
   ```bash
   MONGO_PASS=$(cat data/secrets/mongo_password)
   podman exec -it wip-mongodb mongosh -u wip_admin -p "$MONGO_PASS"
   ```

2. **Change password:**
   ```javascript
   db.adminCommand({
     updateUser: "wip_admin",
     pwd: "new-secure-password"
   })
   ```

3. **Update secrets file:**
   ```bash
   echo -n "new-secure-password" > data/secrets/mongo_password
   chmod 600 data/secrets/mongo_password
   ```

4. **Update .env:**
   ```
   WIP_MONGO_PASSWORD=new-secure-password
   WIP_MONGO_URI=mongodb://wip_admin:new-secure-password@wip-mongodb:27017/
   ```

5. **Recreate services** (restart does NOT reload env vars):
   ```bash
   podman-compose down && podman-compose up -d
   ```

### PostgreSQL

1. **Connect to PostgreSQL:**
   ```bash
   PGPASSWORD=$(cat data/secrets/postgres_password) \
     podman exec -it wip-postgres psql -U wip -d wip_reporting
   ```

2. **Change password:**
   ```sql
   ALTER USER wip WITH PASSWORD 'new-secure-password';
   ```

3. **Update secrets and .env** (similar to MongoDB)

4. **Recreate reporting-sync** (restart does NOT reload env vars):
   ```bash
   podman-compose down && podman-compose up -d
   ```

---

## NATS Token Rotation

1. **Generate new token:**
   ```bash
   NEW_TOKEN=$(openssl rand -hex 32)
   echo $NEW_TOKEN
   ```

2. **Update secrets file:**
   ```bash
   echo -n "$NEW_TOKEN" > data/secrets/nats_token
   chmod 600 data/secrets/nats_token
   ```

3. **Update config/nats/nats.conf:**
   ```conf
   authorization {
       token: "new-token-here"
   }
   ```

4. **Update .env:**
   ```
   WIP_NATS_TOKEN=new-token-here
   NATS_URL=nats://new-token-here@wip-nats:4222
   ```

5. **Recreate NATS and dependent services** (restart does NOT reload env vars):
   ```bash
   podman-compose down && podman-compose up -d
   ```

---

## Dex Client Secret Rotation

1. **Generate new secret:**
   ```bash
   NEW_SECRET=$(openssl rand -hex 32)
   ```

2. **Update config/dex/config.yaml:**
   ```yaml
   staticClients:
     - id: wip-console
       secret: new-secret-here
   ```

3. **Update secrets file and .env**

4. **Recreate Dex** (restart does NOT reload env vars):
   ```bash
   podman-compose down && podman-compose up -d
   ```

---

## TLS Certificate Rotation

### Self-Signed (Caddy Internal)

Caddy automatically regenerates certificates. To force regeneration:

```bash
# Remove existing certificates
rm -rf data/caddy/pki/

# Restart Caddy
podman restart wip-caddy
```

### Let's Encrypt

Let's Encrypt certificates are automatically renewed by Caddy 30 days before expiration. No manual intervention needed.

To force renewal (e.g., after domain change):
```bash
# Remove certificate cache
rm -rf data/caddy/certificates/

# Restart Caddy
podman restart wip-caddy
```

---

## Rotation Schedule Recommendations

| Secret | Rotation Frequency | Notes |
|--------|-------------------|-------|
| API Keys | Annually or after compromise | More frequent for high-security |
| Database passwords | Annually | Coordinate with maintenance windows |
| NATS token | Annually | Brief service disruption |
| Dex client secret | Annually | Affects login sessions |
| TLS certificates | Automatic | Let's Encrypt: 90 days, auto-renewed |

---

## Emergency Rotation

If a secret is compromised:

1. **Immediately rotate** the affected secret
2. **Review logs** for unauthorized access
3. **Notify affected users** if applicable
4. **Update monitoring** to alert on the old secret usage
5. **Document incident** for future reference

```bash
# Quick full rotation (regenerates everything)
./scripts/setup.sh --preset <current-preset> --hostname <hostname> --prod -y
```

Warning: Full rotation requires updating all clients with new credentials.
