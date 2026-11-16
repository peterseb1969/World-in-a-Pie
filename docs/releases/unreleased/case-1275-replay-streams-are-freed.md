---
section: fixes
cases: [1275]
---

### Replay streams are freed when nothing can address them

`cancel` was the only path that deleted a replay's NATS stream. A completed or
failed session kept its stream until the 24-hour message age expired, and after a
restart the session state was gone — so streams from earlier runs belonged to no
session anyone could list, drain or cancel, and accumulated by name.

Now a stream is deleted when its session leaves the API's reach: forgotten by the
bounded session history, or swept at startup, where every `WIP_REPLAY_*` stream
NATS still holds belongs to a process that no longer exists. **Completion still
keeps the stream** — letting a consumer catch up is the point of a replay — and
the sweep reports rather than failing when NATS cannot be asked.

The sweep lists streams, which document-store's NATS user may now do
(`$JS.API.STREAM.LIST`, added to its permissions in the component manifest).
**An install picks the permission up on `wip-deploy redeploy --name <install>`**;
until then the sweep logs a permissions refusal at startup and deletes nothing,
and everything else works as before.

Starting a replay whose stream NATS will not create is refused as an outage
(503) instead of a bare 500. The MCP client no longer sends a replay filter
without a namespace, which the server refuses: it applies the default and, if
none can be resolved, says so before posting.
