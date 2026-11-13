---
section: action-required
cases: [1254]
packages: [wip-client]
---

### @wip/client transport: whole-request timeout, onAuthError on 401 only, one error-code reader

- **The timeout covers the response body.** A response whose body stalls
  after the headers is aborted at the configured `timeout`
  (`WipNetworkError: Request timed out`); before, only the headers were
  bounded.
- **`onAuthError` is called on 401 only.** A 403 refuses an action to a valid
  identity, and signing in again cannot change that. The cached API-key
  headers are still dropped on both.
- **`wipErrorCode(err)` reads every shape:** a bulk item's `errorCode`, a
  top-level `error_code`, and one nested under FastAPI's `detail` object.
  `defStore.createTerm` now carries `errorCode` and `details` (e.g.
  `already_exists`) on the error it throws.
- **`ApiKeyAuthProvider.setApiKey` is removed.** It could not reach the
  transport's cached headers, so a live client went on sending the old key.
  Rotate with `client.setAuth(new ApiKeyAuthProvider(newKey))`.
- **Docstrings now match the services:** `createTemplate` (create is an
  upsert; `on_conflict` changes nothing), the reporting query/CSV export
  (any key; a non-admin key reads only its own namespaces), and
  `deactivateEntry` (the entry stops resolving).

**Action:** an `onAuthError` handler that relied on 403 must handle 403
where the error surfaces. Callers of `setApiKey` switch to `setAuth`.
