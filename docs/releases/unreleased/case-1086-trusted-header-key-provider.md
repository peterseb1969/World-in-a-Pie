---
section: fixes
cases: [1086]
packages: [wip-auth]
---

### `TrustedHeaderProvider` matches keys only through the API-key provider; at most two bcrypt threads run at once, a cancelled request's included

- **`TrustedHeaderProvider(key_provider, header_name=…, default_groups=…)` now requires the `APIKeyProvider`** it matches the proxy key through. It no longer takes `keys` or `hash_salt`. Its fallback, used when no provider was passed, walked a key list frozen at construction under one salt, so it could match no runtime key and ran bcrypt with no caches and no cap. That fallback is removed. `create_providers_from_config` already passed a provider, so services are unchanged.
- **The slow-path cap now bounds bcrypt threads, not waiting requests.** A request cancelled while its bcrypt check runs used to give its permit back at once, while the thread ran on, so more than two checks could run together. The permit now stays taken until the thread finishes.

**Action:** code that constructs `TrustedHeaderProvider` itself passes the `APIKeyProvider` as its first argument and drops `keys=` / `hash_salt=`.
