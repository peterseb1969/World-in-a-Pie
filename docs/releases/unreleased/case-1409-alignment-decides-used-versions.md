---
section: fixes
cases: [1409]
packages: [wip-client]
---

### align_templates decides only the template versions arriving documents use, and reports an outcome for every version

A merge with `align_templates` used to map and judge every archived template
version before it knew which ones the documents use. That caused four
failures:

- A draft nobody activated refused the whole merge when its schema fit a
  target version.
- The version search gave up on every version as soon as one could not be
  placed.
- Under `on_clash=skip`, a document whose identity names another archived
  document, or a file, was not recognised as one the target already holds.
  Its row could then leave its version, and the document, out.
- What happened to a version was split across three reports.

Now:

- **Which versions are decided.** Only versions the arriving documents use,
  and the versions those reference, are decided. Every other archived version
  is history and is never judged.
- **Drafts.** A target draft is never mapped onto. An archived draft that
  arriving documents use refuses the merge.
- **Search.** The search places as many versions as it can. A version that
  fits nowhere is reported `unplaced` without costing the others.
- **Held documents.** These are found by the merge's own document matching,
  after files and documents are mapped to the target, under every clash
  policy. Only rows the merge writes count: a document the target keeps
  (`skip`, an identical copy, or under `newer` an archive copy that is not
  the newer one) counts none, and one the archive wins under `overwrite` or
  `newer` counts only its head row, the one appended.
- **Report.** Each version in `template_alignment.templates[].versions[]`
  carries an `outcome`: `mapped`, `unplaced`, `held`, `history` or
  `refused`. It also carries `held_rows`. A merge refused while settling the
  alignment carries the same `template_alignment` account on its refusal.

An aligned merge with documents now plans files and documents twice: once to
find the documents the target holds, once for the merge.
