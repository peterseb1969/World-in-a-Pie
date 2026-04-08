Generate or update standardized documentation for a constellation app. Run this after Phase 4, after significant `/improve` sessions, and before any handoff or long pause.

### Why this exists

The AI that built this app will not remember building it. The next session — whether it's the same AI, a different AI, or a human developer — starts cold. Without documentation, every session begins with reading source files, guessing at architecture, and making changes that contradict forgotten decisions.

Documentation is not a nice-to-have. It is the app's memory. It is what makes the app maintainable beyond its first session.

### Required files

Every constellation app must have these files in `apps/{app-name}/`:

- `README.md` — what this app is and how to run it
- `ARCHITECTURE.md` — how the code is structured and why
- `WIP_DEPENDENCIES.md` — what WIP entities this app uses
- `IMPORT_FORMATS.md` — what data formats are supported (if applicable)
- `KNOWN_ISSUES.md` — what's incomplete, broken, or intentionally deferred
- `CHANGELOG.md` — what changed, when, and why

Plus inline JSDoc comments on all exported components, hooks, and import parsers.

### Procedure

You MUST Read `docs/playbooks/document.md` before generating or updating any documentation file. The playbook contains the per-file content specifications, the inline-documentation requirements, the "when to run" guidance, and the 5-criterion definition of "well-documented." Do not guess the file contents from memory.

Then execute the playbook against the current state of the app.
