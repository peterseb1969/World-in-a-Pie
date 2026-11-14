---
section: fixes
cases: [1275]
packages: [wip-toolkit]
---

### A backup says on its job record what it left out, and a merge counts a draft

- **`include_all_versions` means what it says.** An archive that skipped
  inactive template versions nothing in it needs — or that skipped documents
  altogether — now stamps `include_all_versions: false`. Only an archive that
  really holds every version of every entity stamps `true`, so a reader can
  tell a full-fidelity archive from a lean one without comparing row counts.
- **The count is on the job, not only in the progress stream.** A completed
  backup records `result.mode: "backup"`,
  `result.template_versions_left_out` and, per namespace that omitted any,
  `result.namespaces[<ns>].template_versions_left_out`. The manifest inside the
  archive still names the `(template_id, version)` pairs themselves; the job
  answer means "what did this backup skip?" no longer needs the archive
  downloaded.
- **`wip-toolkit backup` can ask for full fidelity**: `--include-inactive-templates`,
  the CLI's name for the engine option the API and MCP already had. Its help no
  longer promises "every entity in every status and version" — the engine
  carries every entity in every status, and every template version the archive
  needs.
- **A merge no longer treats every status difference as cosmetic.** Retiring a
  template version or a terminology changes nothing about what it accepts, so
  active against inactive stays cosmetic and the target keeps its own standing.
  A `draft` is not a retirement: it marks a definition that was never activated
  and never reference-validated, so a draft on one side against an active
  definition on the other now refuses the pair instead of silently keeping one.
  Both draft, or a retirement, merge as before.
- The unit Mongo fake answers the reads the leave-out scan makes (a bare
  projected `find`, and `aggregate`), so a unit run of `run_backup` exercises
  that scan instead of seeing nothing.
