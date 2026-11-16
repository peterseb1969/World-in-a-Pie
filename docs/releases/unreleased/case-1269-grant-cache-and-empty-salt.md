---
section: fixes
cases: [1269]
packages: [wip-auth]
---

### A grant change is not undone by a check that was already in flight

A permission check that was waiting on the Registry when a grant change
dropped the services' grant cache used to write its answer, fetched before
the change, into the cache afterwards. A revoke that landed during a check
could then be undone on that service for up to the 30-second cache lifetime.
Such an answer is now returned to its caller but not cached, so the next check
asks the Registry again; "drops its cached grants at once" in the operator
guide holds for a change that overlaps a check too.

### A system restore refuses an archive whose salt is empty

An archive carrying an empty runtime-key salt (with the fingerprint of the
empty string) was reported as `salt_adopted: true` while nothing was adopted,
and the restore went on with key hashes that could never authenticate. An
empty salt is now read as no salt: the restore is refused with 409, as for an
archive made under another salt that does not carry it.

The key revoke texts (route description and MCP tool) and the key-sync module
now say "within a few seconds", as the create texts already did.

### Smaller corrections

- **A key check that runs past the runtime-key deadline** answers 503 ("cannot
  verify, retry") instead of 401, and no longer accepts a runtime key once the
  deadline has passed. The deadline was read once before the bcrypt sweep.
- **A key-file import announces a key change only when a key changed.** Taking
  over only a grant announced `keys: true`, so every service re-synced its keys;
  taking over only a reservation announced at all. A grant taken over is still
  announced, as a grant change.
- **A business-key reference that meets a Registry outage** (5xx, connect error,
  timeout on the document's primary-key lookup) fails its item with
  `registry_error` (`details.rule: registry_unavailable`); it used to be
  `reference_not_found`. The change had no release note of its own.
- **A merge's progress line counts documents and versions apart**:
  `[ns] inserted 4 documents (7 versions)`, where it said "7 documents".
- **CI's complexity ratchet compares the whole push**, from the commit the branch
  was at before it, so a C+ function added by an earlier commit of the same push
  is caught; the lint job now checks out full history.
- `wip://conventions` says a key gets `admin` on a namespace it reserves; the
  operator guide says a key-file scope change reaches the other services within
  a few seconds where the install runs NATS.
