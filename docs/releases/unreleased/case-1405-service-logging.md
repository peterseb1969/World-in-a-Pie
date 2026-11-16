---
section: operational
cases: [1405]
packages: [wip-auth]
---

### Every backend service logs at INFO, and `WIP_LOG_LEVEL` sets the level

Registry, def-store, template-store and document-store never configured Python logging. Their INFO lines were dropped, among them runtime API keys created or updated, attachment changes, the namespace deletion lifecycle and document-store's startup integrity check. Their warnings reached the container log without a time, a logger name or a level.

These four services, and reporting-sync, ingest-gateway and auth-gateway, now configure logging the same way, through `wip_auth.configure_logging()`:
- the format is `<time> - <logger> - <LEVEL> - <message>`;
- the level is INFO, or the level `WIP_LOG_LEVEL` names (DEBUG, INFO, WARNING, ERROR, CRITICAL);
- reporting-sync and ingest-gateway keep DEBUG when their debug setting is on and `WIP_LOG_LEVEL` is unset.

"API key used: name=… owner=… endpoint=…" is now logged at INFO only the first time a process sees a key, and after that at most once an hour per key, so it still shows which keys are in use before a rotation. Every use is still logged at DEBUG. It used to be logged at INFO on every request: in the four services above it was dropped, and in the other three it appeared once per request.

httpx and httpcore are held at WARNING or above in every service. Their line per HTTP request made up 95 % of reporting-sync's INFO output, and it is gone from that log too. document-store's "storage-key repair skipped" startup line is now an INFO log record instead of a print.

**Action:** none. The four services' logs grow by their INFO lines. To set a level, put `WIP_LOG_LEVEL` in a service's environment; the deployer does not render one.
