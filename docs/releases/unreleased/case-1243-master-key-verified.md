---
section: fixes
cases: [1243]
---

### The install's master key is never sent over an unverified connection

Several requests carry the master key to the install's public URL: the apply's runtime-reservation and attachment checks, `verify --security --live`, and the `rotate-key` check. On every install but `letsencrypt` they ran with certificate verification off.

They now verify against the install's own trust anchor:

| `tls` | Verified against |
|---|---|
| `internal` | Caddy's root CA |
| `self-signed` (k8s) | the certificate in the install's TLS Secret |
| `letsencrypt`, `external` | the system store |

When no anchor is available yet (a first install, before Caddy has made its CA), the request is skipped and the output says so.
