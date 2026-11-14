---
section: fixes
cases: [1309]
---

### The bootstrap gate template says what failed, and re-checks a refused key

The bootstrap templates (`apps/templates/bootstrap/`) told a new app's users
"WIP is unreachable" for every failure of the start-up check. That covered a
refused API key, a WIP 5xx, a failure of the app's own check, and the app's
own server not answering. On k8s a just-added app's key is refused for a few
minutes, until the mounted key file refreshes, and the gate then stayed on that
message until someone clicked Retry. The templates now tell them apart:

- `wip_unreachable`: no answer from WIP at all.
- `wip_key_refused`: WIP answered 401/403. The gate names the app's key
  (`APP_KEY_NAME`, `app-<manifest name>`) and re-checks it on its own, 5 s
  doubling to 30 s for five minutes, then waits for "Retry now".
- `wip_error`: WIP answered with another error status, which the gate shows.
- `check_failed` / `app_error`: the app's own server failed its check, or did
  not answer.

`checkStatusDetail()` reads the HTTP status from the error the app's `wipGet`
throws (`status`, or `statusCode` as `@wip/client`'s errors carry it).

**Action:** for an app created from these templates, make `wipGet` attach the
status (the server template's header shows how) and set `APP_KEY_NAME`.
Templates are copied when an app is created, so existing apps keep their copy;
WIP-KB already carries the same change. A server that registers views at
start-up should check again after a refused or unreachable first check, not
skip registration (see `ensureRegisteredViewsWithRetry`).
