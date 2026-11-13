---
section: fixes
cases: [1101]
---

### A backup keeps the template versions a carried template nests

A backup leaves out the inactive template versions nothing in the archive
needs. It kept the versions documents pin and the versions a carried template
extends, but not the version a carried template pins as a nested template
(a field's `template_ref_version` or `array_template_ref_version`). An archive
could carry a template naming a nested version it did not carry. After a
restore, nested validation found no such version and left the nested objects
of that template's documents unvalidated, without a warning.

Now every version a carried template pins as a nested template is carried
too, and so is every version those pin in turn. A nested pin with no version
(a draft's) keeps every version of its template. `include_inactive_templates`
still keeps everything.
