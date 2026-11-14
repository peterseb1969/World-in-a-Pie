---
section: fixes
cases: [1297]
---

### A restore's dry run no longer warns about id_config it would apply, and a failed activation reports every target

- A dry run of a restore into a namespace that does not exist yet warned that
  the archive's `id_config` "was NOT applied (it is {} here)". The dry run
  creates no namespace, so there was nothing to read back. It now compares
  the archive's `id_config` with the Registry's dry-run answer, on the
  archive's own entity types and fields.
- A fresh restore writes every target before it activates any. When an
  activation failed, the error and the job's `details.activation` named only
  the targets it had reached. The targets written but not yet activated are
  now listed too, as not activated and among the namespaces to delete.
- A Registry that cannot be reached during activation, or whose answer is not
  a result object, now fails the job with the same per-target report as a
  refused batch, instead of an unexplained error.
- The activation failure also says that the steps after activation (carrying
  the archive's other synonyms over and recording restore provenance) did not
  run for any target, including those already live.
