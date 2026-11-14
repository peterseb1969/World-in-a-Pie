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
restore, every later write to that template's documents was refused
(`template_not_found` for the nested field), and the integrity scan reported
`orphaned_template_ref`.

Now every version a carried template pins as a nested template is carried
too, and so is every version those pin in turn, and a template's `extends`
is resolved the same way (a stored value or `NS:VALUE` form included). A
nested pin with no version (only a row stored before nested pins were
required has one) keeps every version of its template. `include_inactive_templates`
still keeps everything.
