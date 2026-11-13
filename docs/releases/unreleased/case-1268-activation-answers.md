---
section: fixes
cases: [1268]
---

### Keyless activation answers repeats once and says when its write landed

`POST /api/registry/entries/activate` answered every occurrence of a repeated
keyless `entry_id` `activated`, so `activated` counted one entry several
times; the first occurrence is now `activated` and the rest `already_active`,
as keyed items already were. When the keyless write fell short and the
re-read that sorts it out failed, every item was reported as if the write
had failed, though most were live. The re-read is now retried once; if it
fails again each item is an `internal_error` whose message says how many
entries the write activated and that activating again answers
`already_active` for a live one. A fresh restore whose activation fails no
longer calls every failure "a genuine duplicate": it counts the failures by
error code, and only `already_exists` is a duplicate identity.
