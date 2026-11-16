---
section: fixes
cases: [1399]
---

### Ingest: a large bulk answer no longer leaves its written items without an outcome

A bulk write's answer was kept whole in the item's outcome. Past NATS's 1 MB
message limit (about 6,400 terms in one `terms` message) the outcome could
never be published: the items were written, the message was retried for about
16 minutes and then given up, and its producer never found an outcome for the
`correlation_id`.

- A 2xx answer is kept whole up to 512 KiB of JSON. A larger bulk answer, a
  200 whose items all failed included, keeps `total`, `succeeded` and `failed`
  exact and lists `results` in order, each item reduced to `index`, `status`,
  `id`, `error_code`, `document_id` and `version`, as many as fit;
  `results_truncated` says whether any were left out. Any other answer over the
  bound becomes `{"truncated": true, "raw": …}`.
- An outcome still over the connection's `max_payload` is published with its
  answer cut to the counts (`results: []`, `results_truncated: true`) instead
  of being retried.

**Producers:** an outcome's `response` is no longer always the target's full
answer. Rely on the counts and the item `status`; read other per-item fields
only when `results_truncated` is absent, and recover the ids of unlisted items
by value lookup. `docs/wip-deployable-app-contract.md` states the shape.
