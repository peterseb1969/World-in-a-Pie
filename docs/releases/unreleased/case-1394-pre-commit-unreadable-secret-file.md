---
section: fixes
cases: [1394]
---

### The pre-commit gate no longer loses secret values to a file it cannot open

A secret file removed or made unreadable after the gate listed it stopped
the awk that reads them all. The value of the file before it was lost as
well, and the gate passed a commit that carried either. Such a file is now
skipped and named in a note (`pre-commit: note: N secret file(s) could not
be read`), and every other file is still checked. Any other failure to read
them refuses the commit. Apps get the new gate on their next scaffold
refresh, which replaces an unmodified earlier gate.
