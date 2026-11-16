---
section: fixes
cases: [1330]
---

### The app pre-commit gate no longer adds over a second to every commit on a host with many installs

The `.githooks/pre-commit` gate the scaffold installs in every app collected
this host's secret values by reading each file under `~/.wip-deploy/*/secrets/`
in its own subshell, and diffed every staged file twice. On a host with 11
installs (351 secret files), that cost 1.2 s per commit before the gate looked
at what was staged, and 3.4 s for a 60-file commit. The gate now reads every
secret file in one pass and diffs each staged file once: the same commits take
0.08 s and 1.5 s. The checks are unchanged, with two exceptions:

- The credential-shape check now also covers an added line that itself starts
  with `++`. Such a line used to be skipped as if it were the diff's file
  header.
- A secret file containing a NUL byte now contributes what precedes its first
  NUL, with BSD awk, gawk and mawk. BusyBox awk drops the NUL instead, so such
  a secret is not matched there. wip-deploy writes its secrets as text.

A scaffold refresh replaces a `.githooks/pre-commit` that is still this or the
earlier shipped gate byte for byte, and says so. A hook the app has changed is
kept, with a warning naming what the shipped gate now does differently.
