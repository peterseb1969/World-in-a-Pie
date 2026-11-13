---
section: action-required
cases: [1238]
---

### A service refuses to start without the keys it calls other services with

The backend services no longer fall back to the publicly documented dev key
`dev_master_key_for_testing` when a variable naming an outbound key is unset.
Some readers used to send an empty key instead; they no longer do either. Either
way the called service answered 401, and wip-auth reported it as "the Registry
could not be reached" (503), so a missing variable looked like an outage.

Each service now checks every outbound key at startup and refuses to start,
naming each variable that is unset:

| Service | Variables (each chain: the first name, else the ones after it) |
|---|---|
| def-store | `REGISTRY_API_KEY`, `TEMPLATE_STORE_API_KEY` (each else `API_KEY`); `WIP_AUTH_LEGACY_API_KEY` |
| template-store | `REGISTRY_API_KEY`, `DEF_STORE_API_KEY`, `DOCUMENT_STORE_API_KEY` (each else `API_KEY`); `WIP_AUTH_LEGACY_API_KEY` |
| document-store | `REGISTRY_API_KEY`, `TEMPLATE_STORE_API_KEY`, `DEF_STORE_API_KEY` (each else `API_KEY`); `WIP_AUTH_LEGACY_API_KEY` |
| reporting-sync | `API_KEY`; `REGISTRY_API_KEY` (else `API_KEY`); `WIP_AUTH_LEGACY_API_KEY` |
| ingest-gateway | `REGISTRY_API_KEY` (else `WIP_AUTH_LEGACY_API_KEY`, `API_KEY`, `MASTER_API_KEY`) |
| registry | `WIP_AUTH_LEGACY_API_KEY` (else `MASTER_API_KEY`, `API_KEY`, `REGISTRY_API_KEY`) |

`WIP_AUTH_LEGACY_API_KEY` is filled from `MASTER_API_KEY` or `API_KEY` when it
is unset, so setting `MASTER_API_KEY` covers it. A key that goes missing at run
time is answered 500 with `error_code: service_key_missing`, naming the variable.

The MCP server's stdio and local-tooling calls likewise need `WIP_API_KEY`
(or `MASTER_API_KEY`, `API_KEY`, `WIP_API_KEY_FILE`). Without one, a tool call
fails naming them, instead of sending the dev key.

**Action:** none on installs rendered by wip-deploy, which sets every one of
these variables. A hand-written environment (compose, k8s, a local `uvicorn`)
must set each variable the table names for the services it runs.
