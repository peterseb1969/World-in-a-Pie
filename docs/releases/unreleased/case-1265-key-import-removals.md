---
section: fixes
cases: [1265]
---

### What the key import removes (correction)

An earlier note said the key import "never removes a record it did not
import". It never deletes a key it did not import, but it does remove two
kinds of records it did not make: when the key file declares a key's grants,
that key's API-made grants on namespaces the file does not declare; and when
the file drops a managed key, the key goes as a revoke takes it, with all its
grants (API-made included) and its API reservations of namespaces never
created.
