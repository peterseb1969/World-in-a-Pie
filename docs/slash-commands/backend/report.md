Capture a fireside chat, design discussion, or architecture decision for the Field Reporter. Use this when Peter initiates a conversation about scope, design, trade-offs, or direction — not for routine commits.

Also use `/report session-end` to trigger your session summary when work is wrapping up.

### Prerequisites

You must have a session ID and report directory already created (see YAC Reporting section in CLAUDE.md). If you don't have one yet, create it now before proceeding.

### Handling `/report session-end`

If the argument is `session-end`, don't create a fireside report. Instead, write (or overwrite) the Session Summary section in your `session.md` as described in the YAC Reporting instructions. Then confirm to Peter that the summary was written.

### Steps (for fireside chats)

#### 1. Get the current time

```bash
date '+%Y-%m-%d %H:%M'
```

#### 2. Identify the topic

Infer the topic from context. If unclear, ask Peter. Create a short slug: `namespace-deletion-design`, `mutable-terminologies`, `scope-change-auth`.

#### 3. Write the report

Create a file in your report directory:

```
/Users/peter/Development/FR-YAC/reports/<YOUR-SESSION-ID>/report-<topic-slug>.md
```

Structure:

```markdown
---
session: <your session ID>
type: fireside
topic: <short topic name>
time: <from date command above>
participants: Peter, <your session ID>
---

## Context
<What triggered this discussion>

## Options Considered
<Alternatives discussed, if any>

## Decision
<What was decided and why>

## Deferred
<What was explicitly left open>

## Peter's Voice
<Direct quotes — corrections, challenges, insights. Omit if nothing quotable.>

## Impact
<How this affects current work, other apps, or the platform>
```

#### 4. Confirm

Tell Peter the report was written and what file it's in. Continue with the session's work.

### When to use this

- Peter says "let's talk about..." or initiates a design discussion
- A mid-implementation decision changes direction
- Peter corrects an assumption or challenges an approach
- A cross-app or platform-level issue is identified
- Peter says to wrap up (`/report session-end`)

### When NOT to use this

- Routine bug fixes (those go in `commits.md`)
- Standard phase work (those go in `session.md`)
- Factual Q&A without broader implications
- Peter said "off the record"
