# Metabase for WIP

Self-service BI dashboards connected to WIP's PostgreSQL reporting database.

## Quick Start

### Prerequisites

1. WIP must be running with the reporting module enabled:
   ```bash
   ./scripts/setup.sh --preset analytics  # or --preset full
   ```

2. Verify PostgreSQL is running:
   ```bash
   podman exec wip-postgres pg_isready
   ```

### Start Metabase

```bash
cd deploy/optional/metabase
podman-compose up -d
```

### Access

- **URL:** http://localhost:3030
- **First-time setup:** Follow the Metabase setup wizard

### Connect to WIP Database

During Metabase setup (or via Admin > Databases > Add Database):

| Setting | Value |
|---------|-------|
| Database type | PostgreSQL |
| Display name | WIP Reporting |
| Host | `wip-postgres` (if on same network) or your WIP host |
| Port | `5432` |
| Database name | `wip_reporting` |
| Username | `wip` |
| Password | `wip_dev_password` (or your production password) |
| Use secure connection (SSL) | **OFF** |

## Configuration

### Environment Variables

Create a `.env` file to customize:

```bash
# Port mapping (default: 3030)
METABASE_PORT=3030

# Data directory for Metabase's internal database
METABASE_DATA_DIR=./data

# Java memory (reduce for Pi, increase for larger deployments)
METABASE_JAVA_OPTS=-Xmx512m

# Optional: Pre-configure setup token for automation
# METABASE_SETUP_TOKEN=your-token-here
```

### Memory Tuning

| Device | Recommended JAVA_OPTS |
|--------|----------------------|
| Pi 4 (2GB) | `-Xmx384m` |
| Pi 4 (4GB) | `-Xmx512m` |
| Pi 5 (8GB) | `-Xmx1g` |
| Server | `-Xmx2g` or more |

## Standalone Deployment

To run Metabase on a different host than WIP:

1. Create `.env.standalone`:
   ```bash
   METABASE_PORT=3030
   METABASE_DATA_DIR=./data
   METABASE_JAVA_OPTS=-Xmx1g
   ```

2. Start without WIP network:
   ```bash
   # Edit docker-compose.yml to remove external network
   # Or create docker-compose.override.yml:
   cat > docker-compose.override.yml << 'EOF'
   services:
     metabase:
       networks:
         - default
   networks:
     wip-network:
       external: false
   EOF

   podman-compose up -d
   ```

3. Connect to WIP PostgreSQL using external hostname/IP:
   - Host: `wip-pi.local` or IP address
   - Port: `5432`
   - Ensure PostgreSQL port is accessible from Metabase host

## WIP Tables

Metabase will discover tables automatically. WIP creates tables like:

| Table | Description |
|-------|-------------|
| `doc_customer` | Documents from CUSTOMER template |
| `doc_product` | Documents from PRODUCT template |
| `doc_order` | Documents from ORDER template |
| ... | One table per template with sync enabled |

### Table Structure

Each `doc_*` table includes:

| Column | Description |
|--------|-------------|
| `document_id` | Primary key (UUID) |
| `template_id` | Template reference |
| `template_version` | Template version number |
| `version` | Document version |
| `status` | active/inactive |
| `identity_hash` | For deduplication |
| `created_at` | Creation timestamp |
| `created_by` | Creator identity |
| `updated_at` | Last update timestamp |
| `<field_name>` | Each template field becomes a column |
| `<field_name>_term_id` | Term ID for term fields |
| `data_json` | Full document data as JSONB |

## Example Queries

### Documents by Template

```sql
SELECT
  table_name,
  (xpath('/row/cnt/text()',
    query_to_xml(format('SELECT count(*) as cnt FROM %I', table_name), false, true, '')
  ))[1]::text::int as count
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'doc_%';
```

### Recent Activity

```sql
SELECT
  template_id,
  COUNT(*) as docs_created,
  DATE(created_at) as date
FROM doc_customer
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY template_id, DATE(created_at)
ORDER BY date DESC;
```

### Term Distribution

```sql
SELECT
  country,
  country_term_id,
  COUNT(*) as count
FROM doc_customer
WHERE status = 'active'
GROUP BY country, country_term_id
ORDER BY count DESC
LIMIT 20;
```

## Pre-built Dashboards

Coming soon: Import pre-built dashboards from `dashboards/` directory.

```bash
# Future: Import dashboard
curl -X POST http://localhost:3030/api/card \
  -H "X-Metabase-Session: $SESSION" \
  -d @dashboards/wip-overview.json
```

## Backup

Metabase stores its configuration (dashboards, questions, users) in an H2 database:

```bash
# Backup Metabase data
cp -r data/ backup/metabase-$(date +%Y%m%d)/

# Or if using external PostgreSQL for Metabase:
pg_dump -U metabase metabase > metabase-backup.sql
```

## Production Considerations

For production deployments:

1. **Use PostgreSQL for Metabase's internal database:**
   ```yaml
   environment:
     MB_DB_TYPE: postgres
     MB_DB_HOST: your-postgres-host
     MB_DB_PORT: 5432
     MB_DB_DBNAME: metabase
     MB_DB_USER: metabase
     MB_DB_PASS: secure-password
   ```

2. **Enable HTTPS** via reverse proxy (Caddy/nginx)

3. **Configure authentication** (LDAP, SAML, or Google OAuth)

4. **Set up regular backups** of Metabase database

5. **Monitor memory usage** and adjust JAVA_OPTS

## Troubleshooting

### Metabase won't start

Check logs:
```bash
podman logs wip-metabase
```

Common issues:
- Not enough memory: Reduce `JAVA_OPTS`
- Port conflict: Change `METABASE_PORT`
- Network not found: Ensure WIP is running first

### Can't connect to WIP PostgreSQL

1. **Disable SSL:** Make sure "Use a secure connection (SSL)" is **OFF** in Metabase. WIP's PostgreSQL doesn't use SSL by default. Do not use `sslmode=disable` in connection options - just turn off the SSL toggle.

2. Verify PostgreSQL is running:
   ```bash
   podman exec wip-postgres pg_isready
   ```

3. Check network connectivity:
   ```bash
   podman exec wip-metabase ping wip-postgres
   ```

4. Verify credentials in Metabase database settings

### Slow queries

- WIP tables may lack indexes for complex queries
- Consider adding indexes via PostgreSQL directly
- Use Metabase's query caching feature

## Uninstall

```bash
cd deploy/optional/metabase
podman-compose down
rm -rf data/  # Remove Metabase data (dashboards, users, etc.)
```
