---
section: fixes
cases: [1086]
packages: [wip-auth]
---

### A request that asserts an identity is refused when its key check finds no room, instead of served as the key

A service with `WIP_AUTH_TRUST_PROXY_HEADERS=true` requires a privileged `X-API-Key` alongside `X-WIP-User`, and that key check is bounded: two bcrypt threads, at most eight checks waiting behind them. When the proxy-key check found no room, the request was handed to the next provider with the **same** `X-API-Key` — and the bound is judged per call, so the key could verify there. The request then went forward as the key itself: a caller holding a `wip-services` or `wip-admins` key with `X-WIP-User: alice` was served with the key's own groups, and alice was dropped without a trace.

Such a request is now refused where the refusal happens, with **503** and `Retry-After: 1`. Nothing was cached about the key and no bcrypt ran, so once there is room again the same request asserts the identity it carried. A key that answers and is simply not privileged still falls through and authenticates as itself, which is how an app's own key keeps forwarding a user identity it may not vouch for.
