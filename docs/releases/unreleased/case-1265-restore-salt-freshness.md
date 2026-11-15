---
section: fixes
cases: [1265]
---

### A stack whose managed key keeps its hash does not adopt another salt

A system restore adopts the archive's API-key salt only on a fresh stack,
and counted every key the install's key file manages as safe, because the
import re-hashes it. It re-hashes only a key the file gives by its plaintext:
one given by `key_hash`, or any managed key while the key file is refused,
missing or not configured, kept its old hash and stopped authenticating
after the adoption. Such a stack is now not fresh, and the restore is refused
(409 `salt_mismatch`) instead.
