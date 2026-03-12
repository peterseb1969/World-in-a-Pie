# NATS JetStream Configuration

WIP uses NATS with JetStream for reliable message delivery between services. The primary use case is syncing document events from Document Store to the Reporting Sync service for PostgreSQL replication.

## Security

> **Current Status:** NATS runs **without authentication** in development mode. Anyone who can reach port 4222 can subscribe to events.

| Profile | Port Exposure | Risk | Recommendation |
|---------|---------------|------|----------------|
| Mac (localhost) | localhost only | Low | OK for development |
| Pi (network) | LAN accessible | Medium | Restrict network or add auth |
| Production | Potentially internet | High | **Must enable authentication** |

See [Securing NATS for Production](#securing-nats-for-production) below.

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
   podman logs wip-reporting-sync 2>&1 | grep -i error
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

## Custom Subscriptions

The WIP event stream is a powerful integration point. You can create your own consumers to:

- Trigger external workflows (email notifications, webhooks)
- Sync to external systems (Elasticsearch, data warehouses)
- Build real-time dashboards
- Audit logging to external systems
- Custom ETL pipelines

Multiple consumers can subscribe to the same stream independently - each gets its own copy of every message.

### Event Payload Structure

All events follow this structure:

```json
{
  "event_id": "evt-019c20d4-1234-5678-9abc-def012345678",
  "event_type": "document.created",
  "timestamp": "2026-02-03T10:30:00.000Z",
  "source": "document-store",
  "document": {
    "document_id": "019c20d4-67f1-7a8f-989e-74436577ce8d",
    "template_id": "TPL-000001",
    "template_value": "PERSON",
    "version": 1,
    "status": "active",
    "identity_hash": "a1b2c3d4e5f6...",
    "data": {
      "first_name": "John",
      "last_name": "Doe",
      "email": "john.doe@example.com",
      "country": "Germany"
    },
    "term_references": {
      "country": "T-000042"
    },
    "created_at": "2026-02-03T10:30:00.000Z",
    "created_by": "user:admin@wip.local"
  }
}
```

### Event Types

| Subject | Event Type | Description |
|---------|------------|-------------|
| `wip.documents.created` | `document.created` | New document created |
| `wip.documents.updated` | `document.updated` | Document updated (new version) |
| `wip.documents.deleted` | `document.deleted` | Document soft-deleted |
| `wip.templates.created` | `template.created` | New template created |
| `wip.templates.updated` | `template.updated` | Template updated |

### Authentication

> **Development:** No authentication required (open access on localhost).
>
> **Production:** See [Securing NATS for Production](#securing-nats-for-production) for auth setup.

If NATS authentication is enabled, you'll need credentials:

```bash
# Token auth
export NATS_URL="nats://your-token@localhost:4222"

# User/password auth
export NATS_URL="nats://user:password@localhost:4222"
```

### Quick Test with NATS CLI

The fastest way to see events:

```bash
# Subscribe to all WIP events (ephemeral - won't persist position)
nats sub "wip.>" --server="$NATS_URL"

# Subscribe to only document events
nats sub "wip.documents.>"

# Subscribe with headers visible
nats sub "wip.>" --headers
```

### Python Consumer Example

Create a durable consumer that survives restarts:

```python
#!/usr/bin/env python3
"""
Custom WIP Event Consumer

Install: pip install nats-py

Usage: python my_consumer.py
"""

import asyncio
import json
import os
import signal
from datetime import datetime
from nats.aio.client import Client as NATS
from nats.js.api import ConsumerConfig, DeliverPolicy, AckPolicy

# Configuration
NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
# With auth: "nats://user:password@localhost:4222" or "nats://token@localhost:4222"
# From container: "nats://wip-nats:4222"
STREAM_NAME = "WIP_EVENTS"
CONSUMER_NAME = "my-custom-consumer"  # Unique name for your consumer
SUBJECT_FILTER = "wip.documents.>"    # Or "wip.>" for all events


async def handle_message(msg):
    """Process a single message."""
    try:
        event = json.loads(msg.data.decode())

        event_type = event.get("event_type", "unknown")
        doc = event.get("document", {})

        print(f"[{datetime.now().isoformat()}] {event_type}")
        print(f"  Document: {doc.get('document_id')}")
        print(f"  Template: {doc.get('template_value')}")
        print(f"  Data: {json.dumps(doc.get('data', {}), indent=4)}")
        print()

        # === YOUR CUSTOM LOGIC HERE ===
        # Examples:
        # - Send webhook: requests.post(webhook_url, json=event)
        # - Send email: send_notification(event)
        # - Index in Elasticsearch: es.index(index="wip", body=doc)
        # - Write to file: append_to_log(event)

        # Acknowledge successful processing
        await msg.ack()

    except Exception as e:
        print(f"Error processing message: {e}")
        # NAK to trigger redelivery
        await msg.nak()


async def main():
    nc = NATS()

    # Connect to NATS
    await nc.connect(NATS_URL)
    print(f"Connected to NATS at {NATS_URL}")

    # Get JetStream context
    js = nc.jetstream()

    # Create or bind to durable consumer
    # Using pull-based consumer for better control
    try:
        consumer = await js.pull_subscribe(
            subject=SUBJECT_FILTER,
            stream=STREAM_NAME,
            durable=CONSUMER_NAME,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT,
                deliver_policy=DeliverPolicy.ALL,  # Start from beginning
                # Or use DeliverPolicy.NEW for only new messages
            ),
        )
        print(f"Subscribed to {SUBJECT_FILTER} as '{CONSUMER_NAME}'")
    except Exception as e:
        print(f"Error creating consumer: {e}")
        return

    # Handle graceful shutdown
    running = True
    def shutdown(sig, frame):
        nonlocal running
        print("\nShutting down...")
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Process messages
    print("Waiting for messages... (Ctrl+C to exit)")
    while running:
        try:
            # Fetch batch of messages (adjust batch size as needed)
            messages = await consumer.fetch(batch=10, timeout=5)
            for msg in messages:
                await handle_message(msg)
        except asyncio.TimeoutError:
            # No messages available, continue waiting
            pass
        except Exception as e:
            print(f"Error fetching messages: {e}")
            await asyncio.sleep(1)

    # Cleanup
    await nc.close()
    print("Disconnected")


if __name__ == "__main__":
    asyncio.run(main())
```

### Node.js Consumer Example

```javascript
#!/usr/bin/env node
/**
 * Custom WIP Event Consumer
 *
 * Install: npm install nats
 *
 * Usage: node my_consumer.js
 */

const { connect, StringCodec, AckPolicy, DeliverPolicy } = require('nats');

const NATS_URL = 'nats://localhost:4222';
const STREAM_NAME = 'WIP_EVENTS';
const CONSUMER_NAME = 'my-node-consumer';
const SUBJECT_FILTER = 'wip.documents.>';

const sc = StringCodec();

async function handleMessage(msg) {
    try {
        const event = JSON.parse(sc.decode(msg.data));

        const eventType = event.event_type || 'unknown';
        const doc = event.document || {};

        console.log(`[${new Date().toISOString()}] ${eventType}`);
        console.log(`  Document: ${doc.document_id}`);
        console.log(`  Template: ${doc.template_value}`);
        console.log(`  Data: ${JSON.stringify(doc.data, null, 2)}`);
        console.log();

        // === YOUR CUSTOM LOGIC HERE ===

        // Acknowledge
        msg.ack();

    } catch (e) {
        console.error('Error processing message:', e);
        msg.nak();
    }
}

async function main() {
    // Connect to NATS
    const nc = await connect({ servers: NATS_URL });
    console.log(`Connected to NATS at ${NATS_URL}`);

    // Get JetStream
    const js = nc.jetstream();

    // Create durable consumer
    const consumer = await js.consumers.get(STREAM_NAME, CONSUMER_NAME).catch(async () => {
        // Consumer doesn't exist, create it
        const jsm = await nc.jetstreamManager();
        await jsm.consumers.add(STREAM_NAME, {
            durable_name: CONSUMER_NAME,
            ack_policy: AckPolicy.Explicit,
            deliver_policy: DeliverPolicy.All,
            filter_subject: SUBJECT_FILTER,
        });
        return js.consumers.get(STREAM_NAME, CONSUMER_NAME);
    });

    console.log(`Subscribed as '${CONSUMER_NAME}'`);
    console.log('Waiting for messages... (Ctrl+C to exit)');

    // Process messages
    const iter = await consumer.consume();
    for await (const msg of iter) {
        await handleMessage(msg);
    }
}

main().catch(console.error);
```

### Bash/Shell Webhook Forwarder

For simple webhook forwarding without writing code:

```bash
#!/bin/bash
# Forward WIP events to a webhook
# Requires: nats CLI, jq, curl

WEBHOOK_URL="https://your-webhook.example.com/wip-events"
NATS_URL="nats://localhost:4222"

nats sub "wip.documents.>" --server="$NATS_URL" | while read -r line; do
    # Skip non-JSON lines
    if echo "$line" | jq . >/dev/null 2>&1; then
        echo "Forwarding event to webhook..."
        curl -s -X POST "$WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "$line"
    fi
done
```

### Consumer Best Practices

1. **Use unique consumer names** - Each integration should have its own durable consumer name to track progress independently.

2. **Choose the right deliver policy**:
   - `DeliverPolicy.ALL` - Process all historical messages (good for initial sync)
   - `DeliverPolicy.NEW` - Only new messages from now on
   - `DeliverPolicy.LAST` - Start from the last message
   - `DeliverPolicy.BY_START_TIME` - Start from a specific timestamp

3. **Handle errors gracefully**:
   - `ack()` - Message processed successfully
   - `nak()` - Processing failed, redeliver
   - `term()` - Don't redeliver (poison message)

4. **Batch processing** - Fetch messages in batches for better throughput.

5. **Idempotent processing** - Messages may be redelivered; use `document_id` + `version` to detect duplicates.

6. **Monitor your consumer**:
   ```bash
   # Check consumer lag
   nats consumer info WIP_EVENTS my-custom-consumer
   ```

### Filter by Template

To process only specific document types:

```python
# In your message handler
async def handle_message(msg):
    event = json.loads(msg.data.decode())
    doc = event.get("document", {})

    # Only process PERSON documents
    if doc.get("template_value") != "PERSON":
        await msg.ack()  # Acknowledge but skip
        return

    # Process PERSON documents...
```

Or use subject filtering when subscribing:

```python
# Subscribe to specific template events (if published to template-specific subjects)
# Note: Current implementation uses generic subjects, so filter in handler
```

### Running Inside Docker/Podman

If running your consumer in a container on the WIP network:

```yaml
# docker-compose.yml for your consumer
version: "3.8"

services:
  my-consumer:
    build: .
    environment:
      - NATS_URL=nats://wip-nats:4222
    networks:
      - wip-network

networks:
  wip-network:
    external: true
```

## Securing NATS for Production

### Option 1: Token Authentication (Simple)

Create `config/nats/nats.conf`:

```conf
# NATS Server Configuration with Token Auth
port: 4222
http_port: 8222

# JetStream
jetstream {
    store_dir: /data
}

# Simple token authentication
authorization {
    token: "your-secret-token-here"
}
```

Update `docker-compose.infra.yml`:

```yaml
nats:
  image: docker.io/library/nats:2.10
  container_name: wip-nats
  command: ["--config", "/etc/nats/nats.conf"]
  volumes:
    - ./config/nats/nats.conf:/etc/nats/nats.conf:ro
    - ${WIP_DATA_DIR:-./data}/nats:/data
  # ... rest of config
```

Connect with token:

```python
# Python
await nc.connect("nats://localhost:4222", token="your-secret-token-here")
```

```javascript
// Node.js
const nc = await connect({ servers: "nats://localhost:4222", token: "your-secret-token-here" });
```

```bash
# NATS CLI
nats sub "wip.>" --server="nats://localhost:4222" --creds-token="your-secret-token-here"
```

### Option 2: User/Password Authentication

```conf
# config/nats/nats.conf
port: 4222
http_port: 8222

jetstream {
    store_dir: /data
}

authorization {
    users: [
        # Internal services (full access)
        { user: "wip-services", password: "$NATS_SERVICES_PASSWORD",
          permissions: { publish: ">", subscribe: ">" } },

        # External consumers (read-only)
        { user: "wip-consumer", password: "$NATS_CONSUMER_PASSWORD",
          permissions: { subscribe: "wip.>" } },

        # Read-only monitoring
        { user: "wip-monitor", password: "$NATS_MONITOR_PASSWORD",
          permissions: { subscribe: "_INBOX.>" } }
    ]
}
```

### Option 3: NKey Authentication (Most Secure)

NKeys use public-key cryptography (Ed25519). Generate keys:

```bash
# Install nk tool
go install github.com/nats-io/nkeys/nk@latest

# Generate operator, account, and user keys
nk -gen operator -pubout > operator.pub
nk -gen account -pubout > account.pub
nk -gen user -pubout > user.pub
```

See [NATS Security Documentation](https://docs.nats.io/running-a-nats-service/configuration/securing_nats) for full NKey setup.

### Environment Variables for Services

When auth is enabled, update service configurations:

```bash
# .env
NATS_URL=nats://wip-services:services-password@wip-nats:4222
# Or with token:
NATS_URL=nats://your-secret-token@wip-nats:4222
```

### Network-Level Security (Alternative)

If NATS auth is not configured, restrict access at the network level:

1. **Don't expose port 4222 externally** - Remove from docker-compose ports or bind to localhost only:
   ```yaml
   ports:
     - "127.0.0.1:4222:4222"  # localhost only
   ```

2. **Use container network only** - Services on `wip-network` can reach NATS, external clients cannot.

3. **Firewall rules** - Block port 4222 from external access:
   ```bash
   # UFW (Ubuntu)
   sudo ufw deny 4222

   # iptables
   sudo iptables -A INPUT -p tcp --dport 4222 -j DROP
   ```

### Checking Current Security

```bash
# Test if NATS is open (should fail if secured)
nats sub "wip.>" --server="nats://localhost:4222"

# If this works without credentials, NATS is unsecured
```

## Configuration Reference

Stream and consumer settings are defined in:
- `components/reporting-sync/src/reporting_sync/main.py` (stream creation)
- `components/reporting-sync/src/reporting_sync/worker.py` (consumer creation)

Environment variables:
- `NATS_URL` - NATS server URL (default: `nats://wip-nats:4222`)
