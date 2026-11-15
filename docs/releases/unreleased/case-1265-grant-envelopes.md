---
section: fixes
cases: [1265]
---

### Grant revoke and activate count every item, and an activated grant drops its namespace id

`DELETE /namespaces/{prefix}/grants` and `POST …/grants/activate` counted an
item answered `not_found` as neither succeeded nor failed, so `succeeded +
failed` did not add up to `total`. It now counts as failed. A grant a
platform admin sets through a key's grants, or the key-file import declares
in its file, no longer keeps the `namespace_id` of the orphan it was: an effective
grant carries none.
