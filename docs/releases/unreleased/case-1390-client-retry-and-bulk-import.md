---
section: fixes
cases: [1390]
packages: [wip-client, wip-react]
---

### @wip/client retries exactly 502/503/504; useBulkImport reports a failed batch in its result, not as an error

A GET answered 505 or above (an unsupported HTTP version, insufficient storage, a loop, a captive portal) is no longer retried with backoff: only 502, 503 and 504, which a retry can clear, are, as the client's documentation already said. A batch whose request fails during `bulkImport` is counted in `failed` and listed in `rejected` (`{ offset, size, error }`), and the run resolves; it stops early only under `continueOnError: false`. `useBulkImport` therefore settles as a success when a batch request fails: apps that relied on `isError` to see a failed import read `data.failed` and `data.rejected` instead. The hook still refreshes the cache after every run. The `@wip/client` README no longer says a `WipValidationError`'s `statusCode` always reads 422: it is the status the server answered, 400 or 422.
