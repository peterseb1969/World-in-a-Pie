---
section: fixes
cases: [1367, 1369]
---

### Replay sessions and backup jobs hide what a caller cannot read; activating a template takes write on its own namespace

- **document-store:** `GET`/`DELETE /replay/{session_id}` (and pause/resume) and
  every `/backup/jobs/{job_id}` door answered a caller who cannot read the
  namespace with `Namespace not found`, while an id that names nothing answered
  `Replay session <id> not found` / `Backup job <id> not found`: the difference
  confirmed that the session or job exists. Both now answer exactly as a missing
  id; a caller who can read the namespace but lacks the permission the door needs
  gets 403.
- **template-store:** `POST /templates/{id}/activate` checked write on the
  `namespace` parameter only. It now also takes write on the namespace the
  template lives in, as every other write by id does; a key that writes one
  namespace and only reads the template's gets 403.
