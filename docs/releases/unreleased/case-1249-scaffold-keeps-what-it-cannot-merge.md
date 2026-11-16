---
section: fixes
cases: [1249]
---

### A scaffold refresh no longer destroys what it cannot merge

- An `.mcp.json` that is not valid JSON or not the expected shape is left
  untouched. The scaffold's version goes to `.mcp.json.refresh`, which is
  gitignored.
- A commands directory whose source is missing or empty stops the run. It
  no longer deletes every installed command.
- The bootstrap templates, the query preset and `.env` are create-only in
  the engine itself: a refresh writes only the files that do not exist yet.
- The slim profile's post-compaction hook strips the card header up to
  the first `-->`, wherever it sits on the line. When no card text is
  left, it prints the core invariant.
