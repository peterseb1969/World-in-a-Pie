# NATS JetStream Configuration

WIP uses NATS with JetStream for reliable message delivery between services. The primary use case is syncing document events from Document Store to the Reporting Sync service for PostgreSQL replication.

## Architecture

```
Document Store                    NATS JetStream                 Reporting Sync
─────────────                    ──────────────                 ──────────────

  Create/Update    ──publish──►  WIP_EVENTS stream  ──consume──►  Transform
  Document                       (persistent)                     & Upsert
                                      │                           to PostgreSQL
                                      │
                                 Messages retained
                                 until limits hit
```

## Stream Configuration

The `WIP_EVENTS` stream is created automatically by the reporting-sync service on startup.

| Setting | Value | Description |
|---------|-------|-------------|
| `name` | WIP_EVENTS | Stream name |
| `subjects` | `wip.>` | All WIP events (wildcard) |
| `retention` | limits | Keep messages until limits are hit |
| `max_msgs` | 1,000,000 | Maximum number of messages |
| `max_bytes` | 1 GB | Maximum storage size |
| `max_age` | *not set* | No time-based expiry (messages stay until limits) |
| `storage` | file | Persisted to disk (survives restarts) |

### Subject Hierarchy

Events are published to subjects following this pattern:

```
wip.documents.created    # New document created
wip.documents.updated    # Document updated (new version)
wip.documents.deleted    # Document soft-deleted
wip.templates.created    # New template created
wip.templates.updated    # Template updated
```

## Consumer Configuration

The reporting-sync service creates a durable consumer:

| Setting | Value | Description |
|---------|-------|-------------|
| `name` | reporting-sync-durable | Consumer name |
| `durable_name` | reporting-sync-durable | Survives disconnects |
| `ack_policy` | explicit | Messages must be acknowledged |
| `ack_wait` | 30 seconds | Time before redelivery |
| `max_deliver` | 5 | Max redelivery attempts |
| `deliver_policy` | all | Start from first message |

## Monitoring

### HTTP Monitoring Endpoint

NATS exposes monitoring on port 8222:

```bash
# Basic JetStream stats
curl http://localhost:8222/jsz

# Detailed stream info
curl http://localhost:8222/jsz?streams=true

# Stream + consumer details
curl http://localhost:8222/jsz?streams=true&consumers=true
```

### Key Metrics

| Metric | Location | Healthy Value |
|--------|----------|---------------|
| `messages` | `/jsz` | Total messages in stream |
| `pending_messages` | `/metrics/consumer` | 0 (all processed) |
| `ack_pending` | `/jsz?consumers=true` | 0 (all acknowledged) |
| `num_redelivered` | `/jsz?consumers=true` | Low (some OK from restarts) |
| `api.errors` | `/jsz` | Low (startup errors normal) |

### Reporting Sync Metrics

The reporting-sync service provides additional monitoring:

```bash
# Consumer state
curl http://localhost:8005/metrics/consumer

# Full metrics with per-template stats
curl http://localhost:8005/metrics

# Active alerts
curl http://localhost:8005/alerts
```

### Real-time Monitoring

```bash
# Watch message count
watch -n 2 'curl -s http://localhost:8222/jsz | jq .messages'

# Watch consumer lag
watch -n 2 'curl -s http://localhost:8005/metrics/consumer | jq .pending_messages'
```

## Message Retention

Messages are retained based on limits, not consumption. After a consumer processes a message, it remains in the stream until:

1. **Message count limit** (1 million) is reached - oldest messages deleted
2. **Storage limit** (1 GB) is reached - oldest messages deleted
3. **Time limit** - not currently configured

This allows:
- Multiple consumers to read the same messages
- Replay from a specific point in time
- Recovery after consumer downtime

### Current Usage

Check current stream usage:

```bash
curl -s http://localhost:8222/jsz | jq '{messages, bytes: .storage, max_bytes: .config.max_storage}'
```

## Error Handling

### Delivery Failures

| Scenario | Behavior | Detection |
|----------|----------|-----------|
| Consumer disconnects | Messages queue up, delivered on reconnect | `pending_messages > 0` |
| Processing error | Message NAK'd, redelivered | `num_redelivered` increases |
| Max retries exceeded | Message marked as failed | Logged as error |
| Consumer permanently gone | Messages accumulate | Alert: `processing_stalled` |

### Alert Thresholds

Configure via reporting-sync API:

```bash
curl -X PUT http://localhost:8005/alerts/config \
  -H "Content-Type: application/json" \
  -d '{
    "queue_lag_threshold": 1000,
    "error_rate_threshold": 0.05,
    "stall_timeout_seconds": 300
  }'
```

### Redelivery

The 601 redelivered messages typically seen after restarts are normal - these are messages that were delivered but not yet acknowledged when the consumer disconnected.

## Troubleshooting

### Messages Not Being Processed

1. Check consumer is running:
   ```bash
   curl -s http://localhost:8005/health
   ```

2. Check pending messages:
   ```bash
   curl -s http://localhost:8005/metrics/consumer | jq .pending_messages
   ```

3. Check for errors in logs:
   ```bash
   podman logs wip-reporting-sync-dev 2>&1 | grep -i error
   ```

### High Redelivery Count

Normal causes:
- Service restarts
- Slow processing (ack_wait timeout)

Check consumer logs for processing errors.

### Stream Growing Too Large

If approaching limits, consider:

1. Adding time-based retention (requires code change):
   ```python
   max_age=7 * 24 * 60 * 60  # 7 days
   ```

2. Manually purging old messages:
   ```bash
   # Via NATS CLI
   nats stream purge WIP_EVENTS --keep 10000
   ```

## NATS CLI

For advanced debugging, install the NATS CLI:

```bash
# Mac
brew install nats-io/nats-tools/nats

# Linux
curl -L https://github.com/nats-io/natscli/releases/download/v0.1.1/nats-0.1.1-linux-amd64.zip -o nats.zip
unzip nats.zip && sudo mv nats /usr/local/bin/
```

Common commands:

```bash
# List streams
nats stream ls

# Stream info
nats stream info WIP_EVENTS

# Consumer info
nats consumer info WIP_EVENTS reporting-sync-durable

# Watch messages in real-time
nats sub "wip.>" --headers

# Purge stream (careful!)
nats stream purge WIP_EVENTS
```

## Configuration Reference

Stream and consumer settings are defined in:
- `components/reporting-sync/src/reporting_sync/main.py` (stream creation)
- `components/reporting-sync/src/reporting_sync/worker.py` (consumer creation)

Environment variables:
- `NATS_URL` - NATS server URL (default: `nats://wip-nats:4222`)
