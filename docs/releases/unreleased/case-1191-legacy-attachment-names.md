---
section: fixes
cases: [1191]
packages: [wip-client]
---

### An attachment stored with a name ending in a hyphen no longer breaks every attachment read

An attachment name became a DNS label, so it can no longer end with a
hyphen. The rule was put on the stored record, so one attachment stored
before it (`legacy-app-`) made `GET /api/registry/attachments` answer 500.
That listing is what the app edge polls, so the edge then answered 503 for
every attached app. The system backup failed the same way, and the record
could not be deactivated.

The rule now applies where a name is written (attach). A stored record
loads whatever its name, and is listed with `served: false` and
`not_served_reason: name_invalid`. The app edge refuses only that record.
It can be deactivated by name.

**Action:** for an attachment listed as `name_invalid`, deactivate it and
attach the app again under a valid name.
