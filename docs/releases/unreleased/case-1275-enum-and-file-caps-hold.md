---
section: fixes
cases: [1275]
packages: [wip-archive]
---

### `enum` and the file count cap hold on every field they are declared on

A declared `validation.enum` is now checked for `date` and `datetime` values as
it is for string, number, integer and boolean. An enum declared **empty**
admits nothing — it is a whitelist with no entries, and omitting the key is how
"no constraint" is spelled. And `array_file_config.max_files` caps an array of
files the way `file_config.max_files` caps a multi-file field (`max_files`).
An array with no declared item type keeps untyped items, on an inline candidate
definition as on a stored template. `wip-archive`'s offline validator agrees
(`wip verify`, the toolkit's pre-upload checks).
