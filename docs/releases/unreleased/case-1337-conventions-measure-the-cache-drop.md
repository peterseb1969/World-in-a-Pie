---
section: fixes
cases: [1337]
---

### `wip://conventions` measures a cache drop in seconds instead of calling it instant

The served conventions said that when a namespace is deleted, or an entity is
deleted, retired, loses a synonym or is merged, every service drops what it
cached for it "at once" and a name re-created under the same spelling reads
correctly "right away". The drop waits on the NATS hop: it lands within a few
seconds on an install that runs NATS, and on one that does not nothing is
announced and the 30-second cache TTL is the whole mechanism. The text now
states that timescale, as the key and grant paragraphs already do.
