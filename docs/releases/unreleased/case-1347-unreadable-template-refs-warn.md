---
section: fixes
cases: [1347]
---

### Backup: a template row whose references cannot be read is named in a warning

- **What a backup leaves out.** A backup leaves out inactive template versions that nothing in the archive needs. To decide what is needed, it follows each carried template's `extends` and nested pins.
- **The gap.** A legacy row can hold a slot the backup cannot read: `fields` that is not a list, a field that is not an object, a reference that is not a string, or a version that is not an integer. Such a row is skipped without stopping the backup, as before. The version it depends on could then be left out with nothing said.
- **Now:** when a namespace leaves versions out, the backup adds a `warning` to the job naming each carried template version with an unreadable slot. The warning says a version it depends on may be among those left out.
- **Action:** where you see the warning, back up with `include_inactive_templates` to keep every version, or correct the named rows.
