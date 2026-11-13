---
section: fixes
cases: [1268]
---

### A namespace write that finds the namespace gone answers 404

Updating, archiving or restoring a namespace, or changing its
`deletion_mode`, answered 409 "its deletion is in progress" when the
namespace's deletion completed between the read and the write. It now
answers 404 once the namespace is gone; 409 stays for one still locked.

### A system restore skips an attachment another request attached meanwhile

A system restore that replaced an attachment which another request deleted
and attached again under the same name failed the whole restore. The item is
now a `concurrent_write` skip, as an insert that loses already was.
