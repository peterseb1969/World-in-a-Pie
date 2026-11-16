---
section: fixes
cases: [1243]
---

### The install's master key is never sent over an unverified connection

Several requests carry the master key to the install's public URL: the apply's runtime-reservation and attachment checks, `verify --security --live`, and the `rotate-key` check. On every install but `letsencrypt` they ran with certificate verification off.

They now verify against the install's own trust anchor:

| `tls` | Verified against |
|---|---|
| `internal` | Caddy's root CA, read through podman, else docker |
| `self-signed` (k8s) | the certificate in the install's TLS Secret |
| `letsencrypt`, `external` | the system store |

When no anchor is available yet (a first install, before Caddy has made its CA), the request is skipped and the output says so. A certificate that does not verify against the anchor (an `external` terminator with a private CA, say) is the same "could not ask": `rotate-key` then reports its post-rotation check as not run, not as a failed rotation, and exits 0, since the rotation itself completed.

Only a certificate that fails verification counts as "could not ask". Any
other TLS error, such as a connection dropped mid-handshake while services
restart, is retried like a refused connection. A new key the install already
refused stays a failed rotation, even when the old key's probe then fails on
the certificate. `verify --security --live` on a k8s install whose recorded
cluster cannot be selected now prints its report, with the live checks as not
run, and exits 1. Before, it exited 2 with no report.
