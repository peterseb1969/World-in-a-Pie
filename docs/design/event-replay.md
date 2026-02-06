# Event Replay Design

## Overview

Event replay allows any consumer to "catch up" on historical data by replaying stored documents as events through NATS. This is more generic than direct batch sync and supports multiple use cases:

- Adding PostgreSQL reporting to an existing deployment
- Adding Elasticsearch for full-text search
- Rebuilding a consumer after data loss
- Onboarding new external systems via webhooks
- Testing event handlers with real data

## Design Principles

1. **Same event format** - Replay events use identical format to live events
2. **Dedicated stream** - Replay uses separate streams to avoid mixing with live events
3. **Consumer-initiated** - Consumer requests replay, not a global broadcast
4. **Throttled** - Configurable rate limiting to avoid overwhelming consumers
5. **Resumable** - Replay can be paused and resumed
6. **Idempotent** - Consumers handle duplicate events gracefully

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Replay Architecture                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Consumer                    WIP Services                  Storage   │
│                                                                      │
│  ┌──────────┐    1. Request   ┌──────────────┐         ┌─────────┐  │
│  │Reporting │ ──────────────> │Document-Store│ ──────> │ MongoDB │  │
│  │  Sync    │    replay       │   /replay    │  query  │         │  │
│  │          │                 └──────────────┘         └─────────┘  │
│  │          │                        │                              │
│  │          │    2. Events via       │ publish                      │
│  │          │       NATS             ▼                              │
│  │          │ <─ ─ ─ ─ ─ ─ ─  ┌──────────────┐                      │
│  │          │                 │     NATS     │                      │
│  │          │                 │   JetStream  │                      │
│  │          │                 │              │                      │
│  │          │                 │ wip.replay.  │                      │
│  │          │                 │   {session}  │                      │
│  └──────────┘                 └──────────────┘                      │
│                                                                      │
│  ┌──────────┐                                                        │
│  │ Elastic  │  (Same pattern - request replay, receive events)      │
│  │ Search   │                                                        │
│  └──────────┘                                                        │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Replay Session

Each replay request creates a **replay session** with:

```json
{
  "session_id": "replay-abc123",
  "entity_type": "documents",
  "filter": {
    "template_code": "CUSTOMER",
    "status": "active"
  },
  "stream_name": "WIP_REPLAY_abc123",
  "subject_prefix": "wip.replay.abc123",
  "total_count": 15000,
  "throttle_ms": 10,
  "status": "running",
  "progress": {
    "published": 5000,
    "percentage": 33.3
  },
  "created_at": "2024-01-15T10:00:00Z",
  "started_at": "2024-01-15T10:00:01Z",
  "completed_at": null
}
```

## API Design

### Document-Store Replay Endpoints

#### Start Replay Session

```http
POST /api/document-store/replay/start
Content-Type: application/json
X-API-Key: {api_key}

{
  "entity_type": "documents",
  "filter": {
    "template_code": "CUSTOMER",    // optional: specific template
    "status": "active"              // optional: default "active"
  },
  "options": {
    "throttle_ms": 10,              // delay between events (default: 10)
    "batch_size": 100,              // internal batch size (default: 100)
    "include_versions": false       // include all versions (default: false, latest only)
  }
}
```

**Response:**
```json
{
  "session_id": "replay-abc123",
  "stream_name": "WIP_REPLAY_abc123",
  "subject_prefix": "wip.replay.abc123",
  "total_count": 15000,
  "status": "started",
  "subscribe_to": "wip.replay.abc123.>"
}
```

#### Get Replay Status

```http
GET /api/document-store/replay/{session_id}
```

**Response:**
```json
{
  "session_id": "replay-abc123",
  "status": "running",
  "total_count": 15000,
  "published": 7500,
  "percentage": 50.0,
  "started_at": "2024-01-15T10:00:01Z",
  "estimated_completion": "2024-01-15T10:05:00Z"
}
```

#### Pause/Resume Replay

```http
POST /api/document-store/replay/{session_id}/pause
POST /api/document-store/replay/{session_id}/resume
```

#### Cancel Replay

```http
DELETE /api/document-store/replay/{session_id}
```

### Def-Store Replay Endpoints

Similar pattern for terminologies and terms:

```http
POST /api/def-store/replay/start
{
  "entity_type": "terminologies",  // or "terms"
  "filter": {
    "terminology_code": "COUNTRY"  // optional for terms
  }
}
```

## Event Format

Replay events use the **same format as live events** with additional metadata:

```json
{
  "event_type": "document.created",
  "timestamp": "2024-01-15T10:00:05Z",
  "data": {
    "document_id": "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
    "template_id": "TPL-000001",
    "template_code": "CUSTOMER",
    "version": 1,
    "data": {
      "name": "Acme Corp",
      "country": "United States"
    },
    "term_references": {
      "country": "T-000042"
    }
  },
  "metadata": {
    "replay": true,
    "replay_session_id": "replay-abc123",
    "sequence": 5001,
    "total": 15000
  }
}
```

### Event Subjects

```
wip.replay.{session_id}.documents.{template_code}
wip.replay.{session_id}.terminologies
wip.replay.{session_id}.terms.{terminology_code}
wip.replay.{session_id}.complete
```

### Completion Event

When replay finishes:

```json
{
  "event_type": "replay.complete",
  "timestamp": "2024-01-15T10:05:00Z",
  "data": {
    "session_id": "replay-abc123",
    "entity_type": "documents",
    "total_published": 15000,
    "duration_seconds": 300
  }
}
```

## Consumer Workflow

### 1. Request Replay

```python
# Consumer requests replay
response = await http_client.post(
    f"{document_store_url}/api/document-store/replay/start",
    json={
        "entity_type": "documents",
        "filter": {"template_code": "CUSTOMER"}
    }
)
session = response.json()
```

### 2. Subscribe to Replay Stream

```python
# Subscribe to the session-specific replay subject
await js.subscribe(
    subject=f"{session['subject_prefix']}.>",
    stream=session['stream_name'],
    cb=handle_replay_event
)
```

### 3. Process Events

```python
async def handle_replay_event(msg):
    event = json.loads(msg.data)

    if event['event_type'] == 'replay.complete':
        # Replay done, switch to live stream
        await switch_to_live_stream()
        return

    # Process same as live event
    # The replay=true metadata allows special handling if needed
    await process_document_event(event)
```

### 4. Switch to Live Stream

```python
async def switch_to_live_stream():
    # Unsubscribe from replay
    await replay_subscription.unsubscribe()

    # Subscribe to live events
    await js.subscribe(
        subject="wip.documents.>",
        stream="WIP_DOCUMENTS",
        cb=handle_live_event
    )
```

## Handling Deduplication

During replay, a document might be updated via live events. Consumers must handle this:

### Option A: Sequence-based (Recommended)

Each document has a `version` field. Consumer tracks highest version seen:

```python
async def process_document(doc):
    doc_id = doc['document_id']
    version = doc['version']

    # Check if we already have a newer version
    current_version = await get_current_version(doc_id)
    if current_version and current_version >= version:
        return  # Skip older version

    await upsert_document(doc)
```

### Option B: Timestamp-based

Use `updated_at` timestamp to determine which version wins.

### Option C: Replay-then-Live with Gap Handling

1. Note timestamp before starting replay
2. Complete replay
3. Subscribe to live events from noted timestamp
4. Accept potential duplicates (upsert handles it)

## Stream Configuration

### Replay Stream (per session)

```python
stream_config = StreamConfig(
    name=f"WIP_REPLAY_{session_id}",
    subjects=[f"wip.replay.{session_id}.>"],
    retention=RetentionPolicy.WORK_QUEUE,  # Delete after ack
    max_age=3600,  # 1 hour TTL
    storage=StorageType.MEMORY,  # Ephemeral, no persistence needed
)
```

### Why WORK_QUEUE retention?

- Replay is one-time consumption
- No need to persist after consumer acks
- Memory storage for speed
- Auto-cleanup after TTL

## Throttling Strategy

To avoid overwhelming consumers or the network:

```python
async def publish_replay_events(session, documents):
    throttle_ms = session.options.throttle_ms

    for doc in documents:
        event = create_event(doc, session)
        await js.publish(
            subject=f"wip.replay.{session.session_id}.documents.{doc['template_code']}",
            payload=json.dumps(event)
        )

        session.progress.published += 1

        if throttle_ms > 0:
            await asyncio.sleep(throttle_ms / 1000)
```

### Adaptive Throttling (Future Enhancement)

Monitor consumer lag and adjust throttle:
- If consumer falling behind → increase delay
- If consumer keeping up → decrease delay

## Error Handling

### Consumer Disconnects

- Replay stream persists events (within TTL)
- Consumer reconnects and resumes from last ack'd position
- JetStream handles this automatically

### Replay Service Crashes

- Session state stored in MongoDB
- On restart, check for incomplete sessions
- Resume from last published sequence

### Consumer Requests Abort

- Consumer calls DELETE on session
- Replay stops publishing
- Stream cleaned up

## Comparison: Batch Sync vs Event Replay

| Aspect | Batch Sync | Event Replay |
|--------|------------|--------------|
| **Implementation** | HTTP API calls | NATS events |
| **Consumers** | Reporting-Sync only | Any NATS consumer |
| **Code path** | Separate from live | Same as live events |
| **Extensibility** | New sync per consumer | One mechanism for all |
| **Speed** | Faster (direct) | Slightly slower (events) |
| **Complexity** | Simpler per-consumer | More complex infrastructure |
| **Resumability** | Manual (track page) | Automatic (JetStream) |

**Recommendation**: Keep batch sync for simple cases, add event replay for generic consumer onboarding.

## Implementation Phases

### Phase 1: Document Replay (MVP)

- [ ] Replay session management in Document-Store
- [ ] POST /replay/start, GET /replay/{id}, DELETE /replay/{id}
- [ ] Publish documents to session-specific stream
- [ ] Completion event
- [ ] Basic throttling

### Phase 2: Consumer Integration

- [ ] Update Reporting-Sync to use replay for initial sync
- [ ] Deduplication handling
- [ ] Switch-to-live logic

### Phase 3: Full Entity Support

- [ ] Terminology replay in Def-Store
- [ ] Term replay in Def-Store
- [ ] Template replay in Template-Store

### Phase 4: Advanced Features

- [ ] Pause/resume
- [ ] Adaptive throttling
- [ ] Progress webhooks
- [ ] Replay scheduling (off-peak hours)

## Example: Adding PostgreSQL to Existing Deployment

```bash
# 1. Deploy reporting-sync (connects to existing WIP)
podman-compose -f reporting-sync/docker-compose.dev.yml up -d

# 2. Reporting-sync on startup:
#    - Checks if PostgreSQL tables are empty
#    - If empty, requests replay from Document-Store
#    - Processes replay events, building tables
#    - On completion, switches to live events
#    - Continues real-time sync

# No manual intervention needed!
```

## Configuration

### Document-Store

```env
# Replay settings
WIP_REPLAY_ENABLED=true
WIP_REPLAY_MAX_SESSIONS=10
WIP_REPLAY_DEFAULT_THROTTLE_MS=10
WIP_REPLAY_SESSION_TTL_HOURS=24
```

### Consumer (Reporting-Sync)

```env
# Auto-replay on empty database
WIP_AUTO_REPLAY_ON_EMPTY=true
WIP_REPLAY_THROTTLE_MS=10
```
