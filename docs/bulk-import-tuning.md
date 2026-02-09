# Bulk Import Tuning Guide

This guide covers best practices for importing large terminologies (100k+ terms) into WIP.

## Resource Requirements

### Podman VM Memory (Mac)

On macOS, Podman runs inside a virtual machine. The default memory allocation is often insufficient for large imports.

**Check current settings:**
```bash
podman machine inspect | grep -E '(CPUs|Memory|DiskSize)'
```

**Recommended settings:**

| VM Memory | Expected Performance |
|-----------|---------------------|
| 2GB | Struggles with 100k+ imports |
| 4GB | Should handle 200k comfortably |
| 8GB (recommended) | Comfortable for any size import |

**To increase memory:**
```bash
# Stop all containers first
podman-compose -f docker-compose.infra.yml down

# Stop and reconfigure machine
podman machine stop
podman machine set --memory 8192  # 8GB
podman machine start

# Restart infrastructure
podman-compose -f docker-compose.infra.yml up -d
```

### Raspberry Pi

On Pi, there's no VM - the Pi's physical RAM is the limit:

| Pi Model | RAM | Max Comfortable Import |
|----------|-----|------------------------|
| Pi 4 2GB | 2GB | ~50k terms |
| Pi 4 4GB | 4GB | ~200k terms |
| Pi 5 8GB | 8GB | 500k+ terms |

For Pi deployments with large terminologies, consider:
- Using smaller batch sizes
- Importing during off-peak hours
- Breaking very large imports into multiple files

## API Parameters

The import endpoints accept tuning parameters:

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `batch_size` | 1000 | Terms per MongoDB batch |
| `registry_batch_size` | 100 | Terms per registry HTTP call |

### Endpoints

**Direct bulk create:**
```
POST /api/def-store/terminologies/{id}/terms/bulk
    ?batch_size=1000
    &registry_batch_size=100
```

**Import from JSON/CSV:**
```
POST /api/def-store/import-export/import
    ?batch_size=1000
    &registry_batch_size=100
```

**Import from URL:**
```
POST /api/def-store/import-export/import/url
    ?url=https://...
    &batch_size=1000
    &registry_batch_size=100
```

## Tuning for Different Scenarios

### Timeout Errors

If you see `httpx.ReadTimeout` errors:

```bash
# Reduce registry batch size
?registry_batch_size=50

# Or even smaller
?registry_batch_size=25
```

### System Becomes Unresponsive

If the system freezes or Podman stops responding:

1. **Increase Podman VM memory** (see above)
2. Use smaller batch sizes:
   ```bash
   ?batch_size=500&registry_batch_size=50
   ```
3. The system includes automatic throttling (50-100ms pauses between batches)

### Recommended Settings by Import Size

| Terms | batch_size | registry_batch_size | Notes |
|-------|------------|---------------------|-------|
| < 10k | 1000 | 100 | Default settings work fine |
| 10k-50k | 1000 | 100 | Default settings, ensure 4GB+ RAM |
| 50k-200k | 1000 | 100 | Ensure 8GB RAM for Podman VM |
| 200k+ | 500 | 50 | Conservative settings for stability |

## Monitoring Progress

Watch the def-store logs during import:

```bash
podman logs -f wip-def-store
```

You'll see progress like:
```
INFO: Starting bulk import of 200000 terms to TERM-000017
INFO: Processing batch 1/200: terms 1-1000 of 200000
INFO: Batch 1/200 complete: 1000 created, 1000 total so far
INFO: Processing batch 2/200: terms 1001-2000 of 200000
...
INFO: Bulk import complete: 200000 terms created out of 200000 submitted
```

## Architecture Notes

### Why Sub-Batching?

The registry service checks each term against all existing entries using MongoDB `$in` queries. With 200k+ existing entries:

- A batch of 1000 terms → 1000-item `$in` query → slow
- Sub-batches of 100 terms → 100-item `$in` query → faster

### Why Throttling?

Small pauses between batches allow:
- MongoDB to complete background operations
- Memory to be freed (garbage collection)
- Other services to remain responsive

Current throttling:
- 50ms pause between registry sub-batches
- 100ms pause between MongoDB batches

## Troubleshooting

### Import Hangs

1. Check Podman VM memory: `podman machine inspect`
2. Check container memory: `podman stats`
3. Restart def-store: `podman restart wip-def-store`

### Out of Memory

1. Increase Podman VM memory
2. Reduce batch sizes
3. Consider importing in smaller chunks

### Duplicate Detection

The import handles duplicates gracefully:
- `skip_duplicates=true` (default): Skips existing terms
- `update_existing=true`: Updates existing terms (future feature)

Check the response for `skipped` count to see how many duplicates were found.
