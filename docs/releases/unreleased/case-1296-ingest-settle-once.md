---
section: fixes
cases: [1296]
---

### Ingest: a stored outcome is settled once, and fits one message

- An ack that failed after the outcome was stored used to hand the message
  back as an error: an earlier delivery forwarded the item again, and the last
  delivery published a FAILED "internal error" outcome over the stored one. The
  outcome is now kept until the ack succeeds, so the redelivery publishes it
  again and the item is written once.
- A target's error answer is kept in the outcome only up to a bound: the
  `error` text to 500 characters of the answer's `detail`, and `response` to
  64 KiB of JSON (`{"truncated": true, "raw": …}` beyond it; an answer that is
  not a JSON object is kept under `"body"`). Before, a large 422 made an
  outcome too large to publish, and its producer never learned why.
- `messages_gave_up` on `/health` also counts a given-up message whose outcome
  could not be stored, and a failed keep-alive of a queued message is logged.
