---
section: fixes
cases: [1275]
---

### A file's `allowed_templates` restricts what it names, whatever the row spells

A file's restriction is compared against canonical template ids, but rows can
still hold template **values**: stored before the id conversion, or restored from
an older archive. The startup conversion is a single background task, and a row it
missed — because it arrived later, or the task ran while the Registry was down —
matched no template at all, so the file was refused by the very template its
entry named. Reading a file's restriction now resolves what it holds through the
Registry and writes the repaired list back, so a row enforces what it means the
first time it is used. An entry that resolves to nothing keeps its spelling and
therefore matches nothing: the file stays restricted rather than opening up.

An entry that names several templates is no longer reported as naming none — it
is refused as ambiguous, with the candidates, because that is a different fix.

The what-if door `POST /validation/validate-candidate` no longer refuses
restricted files for a placeholder `template_id`: a definition of a brand-new
template has no canonical id yet, so the comparison has no subject, and the
response now says the restriction was not checked instead of reporting the file
as invalid. Files are still checked for existence, status, size and type.
