---
section: fixes
cases: [1376, 1377]
---

### A scaffold refresh keeps a file it cannot read, and says what it did

- An `.mcp.json` or `.claude/settings.json` the scaffold cannot read (for
  example, one left root-owned by an earlier `sudo` run) is left untouched,
  like one it cannot parse. The scaffold's version goes to a `.refresh`
  sidecar, a notice names the file, and the rest of the refresh runs.
  Before this, an unreadable `.mcp.json` or settings file stopped the whole
  run with a Python traceback.
- At the end of a refresh, `create-app-project.sh` no longer says
  `.mcp.json` "has been regenerated" when the scaffold kept it. It names
  the sidecar to merge from instead, and no longer advises adding
  `.mcp.json` to `.gitignore`, which the script already does.
- `python -m wip_scaffold … --dry-run` notes say a sidecar or a merge
  "would" happen. They used to say a file "was written" or "merged" when
  the dry run wrote nothing.
